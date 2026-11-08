import math
from dataclasses import dataclass, field
from html import escape
from itertools import combinations
from typing import Any
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union
from app.exporters.braille import BrailleError, BrailleTranslator, CELL_HEIGHT_MM, CELL_PITCH_MM, CLOSE_NOTE, DOT_DIAMETER_MM, DOT_PITCH_MM, LINE_PITCH_MM, OPEN_NOTE, braille_width, draw_braille, wrap_braille
from app.schemas import Element, Representation

STANDARD_URL = "https://www.brailleauthority.org/sites/default/files/tg/Tactile%20Graphics%20Standards%20and%20Guidelines%202022_a11y.pdf"
STANDARD_SHA256 = "b271f753bb5023b5247c7f7a9f07135250148f6b4f0f89c53984033ded26545d"
BRAILLE_SIZE_URL = "https://brailleauthority.org/size-and-spacing-braille-characters"
MIN_GAP_MM = 3.2
MAX_LABEL_GAP_MM = 6.0
SYMBOL_DIAMETER_MM = 6.4
PRIMARY_LINE_MIN_MM = 12.7
AREA_MIN_MM2 = 25.4 * 25.4 / 4
MARGIN_MM = 12.7
LINE_WIDTH_MM = 0.7
MAX_FEATURES = 80
LINE_STYLES = {"borders": "solid", "rivers": "dashed", "roads": "dotted", "other": "solid", "regions": "solid", "landmarks": "solid", "cities": "solid"}
POINT_STYLES = {"cities": "circle", "landmarks": "triangle", "other": "square"}
MANUAL_CHECKS = [
    {"rule": "1.3, 2.8, 2.12, 2.13", "task": "Compare with the original and the learning task. Verify names, topology, scale, orientation, and educational meaning. This diagram is not calibrated for geographical measurement."},
    {"rule": "2.3, 2.15, 2.19, 5.8, 5.10", "task": "A qualified braille proofreader must verify UEB translation, keys, learner reading level, source language, title, and any surrounding text."},
    {"rule": "2.11, 2.22, 7.3.11, 10.1, 10.2", "task": "Produce a physical sample. Verify tactile discrimination, crossings, textures, line weights, braille dot height, and usability by touch."},
    {"rule": "2.23, 5.8.3.2, 5.12, 10.3", "task": "Print at 100% actual size. Confirm page orientation, facing-page assembly, pagination, and final copy quality. Do not scale the file to fit."},
]


@dataclass
class Issue:
    code: str
    rule: str
    message: str
    element_ids: list[str] = field(default_factory=list)
    severity: str = "error"
    measured_mm: float | None = None
    required_mm: float | None = None


@dataclass
class Feature:
    element: Element
    geometry: BaseGeometry
    ink: BaseGeometry
    kind: str
    pattern: str


@dataclass
class PlacedLabel:
    element_id: str
    text: str
    cells: str
    bounds: BaseGeometry
    keyed: bool = False
    key: str = ""


@dataclass
class KeyEntry:
    kind: str
    name: str
    cells: str
    pattern: str = ""
    key: str = ""
    lines: list[str] = field(default_factory=list)
    height: float = LINE_PITCH_MM


@dataclass
class Page:
    role: str
    svg: str
    recommended_side: str = "either"


@dataclass
class TactileDocument:
    pages: list[Page]
    map_index: int
    report: dict[str, Any]


class TactileValidationError(ValueError):
    def __init__(self, report: dict[str, Any]):
        super().__init__("BANA 2022 production export is blocked by layout errors. Open the validation report and edit the map or page settings.")
        self.report = report


def point_shape(point: Point, pattern: str) -> BaseGeometry:
    x, y = point.x, point.y
    radius = SYMBOL_DIAMETER_MM / 2
    if pattern == "circle":
        return point.buffer(radius, quad_segs=24)
    if pattern == "triangle":
        return Polygon([(x, y - radius), (x + radius, y + radius), (x - radius, y + radius)])
    return box(x - radius, y - radius, x + radius, y + radius)


def element_geometry(element: Element) -> BaseGeometry:
    points = element.geometry.coordinates
    if element.geometry.type == "point":
        return Point(points[0])
    if element.geometry.type == "polygon":
        return Polygon(points)
    if element.geometry.type == "multipart":
        return MultiLineString([points, *element.geometry.parts])
    return LineString(points)


def transform_geometry(geometry: BaseGeometry, rep: Representation, bounds: tuple[float, float, float, float], frame: tuple[float, float, float, float]) -> BaseGeometry:
    scaled = affinity.scale(geometry, xfact=rep.source.width, yfact=rep.source.height, origin=(0, 0))
    left, top, right, bottom = bounds
    x, y, width, height = frame
    span_x, span_y = max(right - left, 1), max(bottom - top, 1)
    padding = SYMBOL_DIAMETER_MM / 2 + LINE_WIDTH_MM
    factor = min((width - 2 * padding) / span_x, (height - 2 * padding) / span_y)
    return affinity.translate(affinity.scale(scaled, factor, factor, origin=(left, top)), xoff=x + padding - left, yoff=y + padding - top)


def geometry_path(geometry: BaseGeometry) -> str:
    if geometry.is_empty:
        return ""
    if isinstance(geometry, (LineString, Polygon)):
        points = list(geometry.exterior.coords) if isinstance(geometry, Polygon) else list(geometry.coords)
        outer = "M" + " L".join(f"{x:.5f},{y:.5f}" for x, y in points) + (" Z" if isinstance(geometry, Polygon) else "")
        if isinstance(geometry, Polygon):
            outer += " ".join(" M" + " L".join(f"{x:.5f},{y:.5f}" for x, y in ring.coords) + " Z" for ring in geometry.interiors)
        return outer
    if hasattr(geometry, "geoms"):
        return " ".join(geometry_path(part) for part in geometry.geoms)
    return ""


def stroke_attributes(pattern: str) -> str:
    dash = ' stroke-dasharray="6.4 3.2"' if pattern == "dashed" else ' stroke-dasharray="0.01 4.5"' if pattern == "dotted" else ""
    return f'stroke="black" stroke-width="{LINE_WIDTH_MM}" stroke-linecap="round" stroke-linejoin="round"{dash}'


def point_svg(x: float, y: float, pattern: str) -> str:
    radius = (SYMBOL_DIAMETER_MM - LINE_WIDTH_MM) / 2
    if pattern == "circle":
        return f'<circle cx="{x:.5f}" cy="{y:.5f}" r="{radius:.5f}" fill="white" {stroke_attributes("solid")}/>'
    if pattern == "triangle":
        points = f"{x:.5f},{y - radius:.5f} {x + radius:.5f},{y + radius:.5f} {x - radius:.5f},{y + radius:.5f}"
        return f'<polygon points="{points}" fill="white" {stroke_attributes("solid")}/>'
    return f'<rect x="{x - radius:.5f}" y="{y - radius:.5f}" width="{radius * 2:.5f}" height="{radius * 2:.5f}" fill="white" {stroke_attributes("solid")}/>'


def svg_page(rep: Representation, content: str, role: str, description: str) -> str:
    output = rep.export_settings
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{output.page_width_mm}mm" height="{output.page_height_mm}mm" viewBox="0 0 {output.page_width_mm} {output.page_height_mm}" role="img" aria-labelledby="title description"><title id="title">{escape(output.tactile_title)} — {escape(role)}</title><desc id="description">{escape(description)}</desc><metadata>BANA 2022 preparation; physical and braille proofreading required; print at actual size.</metadata><defs><pattern id="tactile-dots" width="5" height="5" patternUnits="userSpaceOnUse"><circle cx="2.5" cy="2.5" r="0.6" fill="black"/></pattern><pattern id="tactile-diagonal" width="5" height="5" patternUnits="userSpaceOnUse"><path d="M0 5L5 0" stroke="black" stroke-width="0.4"/></pattern></defs><rect width="100%" height="100%" fill="white"/>{content}</svg>'


def key_entries(features: list[Feature], translator: BrailleTranslator, columns: int) -> list[KeyEntry]:
    entries: dict[tuple[str, str, str], KeyEntry] = {}
    for feature in features:
        if feature.kind == "label":
            continue
        key = feature.kind, feature.element.category, feature.pattern
        name = feature.element.category.replace("_", " ").capitalize()
        cells = translator.translate(name)
        lines = wrap_braille(cells, max(4, columns - 7))
        height = max(2 if feature.kind == "area" else 1, len(lines)) * LINE_PITCH_MM
        entries[key] = KeyEntry(feature.kind, name, cells, feature.pattern, lines=lines, height=height)
    order = {"area": 0, "line": 1, "point": 2}
    return sorted(entries.values(), key=lambda entry: (order[entry.kind], entry.name))


def try_place(feature: Feature, cells: str, obstacles: list[Feature], placed: list[PlacedLabel], frame: BaseGeometry, gap: float) -> BaseGeometry | None:
    width = braille_width(cells)
    height = CELL_HEIGHT_MM
    target = feature.ink
    min_x, min_y, max_x, max_y = target.bounds
    if feature.kind == "area":
        anchor = feature.geometry.representative_point()
    elif feature.kind == "line":
        anchor = feature.geometry.interpolate(0.5, normalized=True)
    else:
        anchor = feature.geometry
    proposals: list[tuple[float, float]] = []
    if feature.kind == "area":
        proposals.append((anchor.x - width / 2, anchor.y - height / 2))
    for clearance in (gap, min(MAX_LABEL_GAP_MM, gap + 1.0)):
        proposals += [(max_x + clearance, anchor.y - height / 2), (min_x - clearance - width, anchor.y - height / 2), (anchor.x - width / 2, min_y - clearance - height), (anchor.x - width / 2, max_y + clearance)]
    if feature.kind == "line":
        for fraction in (0.25, 0.5, 0.75):
            point = feature.geometry.interpolate(fraction, normalized=True)
            for angle in range(0, 360, 45):
                dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
                proposals.append((point.x + dx * (gap + width / 2) - width / 2, point.y + dy * (gap + height / 2) - height / 2))
    for x, y in proposals:
        if width <= frame.bounds[2] - frame.bounds[0]:
            x = max(frame.bounds[0], min(x, frame.bounds[2] - width))
        if height <= frame.bounds[3] - frame.bounds[1]:
            y = max(frame.bounds[1], min(y, frame.bounds[3] - height))
        candidate = box(x, y, x + width, y + height)
        if not frame.covers(candidate):
            continue
        if any(candidate.distance(label.bounds) < gap - 1e-5 for label in placed):
            continue
        failed = False
        for obstacle in obstacles:
            if obstacle.kind == "label":
                continue
            if obstacle is feature and feature.kind == "area" and feature.geometry.covers(candidate.buffer(gap)):
                continue
            if obstacle.ink.distance(candidate) < gap - 1e-5:
                failed = True
                break
        if failed:
            continue
        inside_area = feature.kind == "area" and feature.geometry.covers(candidate.buffer(gap))
        if not inside_area and target.distance(candidate) > MAX_LABEL_GAP_MM + 1e-5:
            continue
        return candidate
    return None


def draw_key(entries: list[KeyEntry], translator: BrailleTranslator, x: float, y: float, columns: int) -> tuple[str, float]:
    content = [draw_braille(OPEN_NOTE + translator.translate("Key:"), x + 6 * CELL_PITCH_MM, y, "Key", translator.table)]
    cursor = y + 2 * LINE_PITCH_MM
    for index, entry in enumerate(entries):
        if entry.kind == "area":
            fill = f"url(#tactile-{entry.pattern})" if entry.pattern != "none" else "white"
            content.append(f'<rect x="{x}" y="{cursor}" width="25.4" height="12.7" fill="{fill}" stroke="black" stroke-width="{LINE_WIDTH_MM}"/>')
        elif entry.kind == "line":
            center_y = cursor + DOT_DIAMETER_MM / 2 + DOT_PITCH_MM
            content.append(f'<path d="M{x} {center_y}h25.4" fill="none" {stroke_attributes(entry.pattern)}/>')
        elif entry.kind == "point":
            content.append(point_svg(x + 2 * CELL_PITCH_MM, cursor + SYMBOL_DIAMETER_MM / 2, entry.pattern))
        elif entry.kind in ("alphabetic", "numeric"):
            content.append(draw_braille(translator.translate(entry.key, "en-ueb-g1.ctb"), x, cursor, entry.key))
        for line_index, line in enumerate(entry.lines):
            key_cells = translator.translate(entry.key, "en-ueb-g1.ctb") if entry.key else ""
            start_cell = (len(key_cells) + 1 if line_index == 0 else 2) if entry.kind in ("alphabetic", "numeric") else (5 if line_index == 0 else 7)
            content.append(draw_braille(line, x + start_cell * CELL_PITCH_MM, cursor + line_index * LINE_PITCH_MM, entry.name, translator.table))
        cursor += entry.height
    content.append(draw_braille(CLOSE_NOTE, x, cursor, "End of key", translator.table))
    return "".join(content), cursor + 2 * LINE_PITCH_MM


def prepare_single_document(rep: Representation, translator: BrailleTranslator | None = None, viewport: tuple[float, float, float, float] | None = None, number_keys: dict[str, str] | None = None) -> TactileDocument:
    output = rep.export_settings
    issues: list[Issue] = []
    translator = translator or BrailleTranslator(output.braille_table)
    number_keys = number_keys or {element.id: str(index + 1) for index, element in enumerate(sorted((element for element in rep.elements if element.label), key=lambda element: element.id))}
    width, height = output.page_width_mm, output.page_height_mm
    columns = min(40, math.floor((width - 2 * MARGIN_MM + 1e-7) / CELL_PITCH_MM))
    rows = min(25, math.floor((height - 2 * MARGIN_MM + 1e-7) / LINE_PITCH_MM))
    content_width = columns * CELL_PITCH_MM
    content_height = rows * LINE_PITCH_MM
    gap = max(MIN_GAP_MM, output.minimum_spacing_mm)
    if columns < 12 or rows < 12:
        issues.append(Issue("page_too_small", "3.4.2, 3.4.4", "This page is too small for the BANA production layout. Use a larger page."))
    title_lines: list[str] = []
    try:
        title_lines = wrap_braille(translator.translate(output.tactile_title), max(columns, 1))
    except BrailleError as error:
        issues.append(Issue("braille_translation", "2.3, 3.4.1", str(error)))
    title_height = max(1, len(title_lines)) * LINE_PITCH_MM + LINE_PITCH_MM
    title = "".join(draw_braille(line, MARGIN_MM + (content_width - braille_width(line)) / 2, MARGIN_MM + index * LINE_PITCH_MM, output.tactile_title, translator.table) for index, line in enumerate(title_lines))
    visible = [element for element in rep.elements if element.visible]
    identities: set[str] = set()
    duplicates: set[str] = set()
    for element in visible:
        if element.id in identities:
            duplicates.add(element.id)
        identities.add(element.id)
    if duplicates:
        issues.append(Issue("duplicate_element_ids", "2.8", "Some source elements share an identifier. Reanalyze the source before exporting numbered references.", sorted(duplicates)))
    if not visible:
        issues.append(Issue("empty_map", "1.3, 2.8", "No visible map features remain."))
    if len(visible) > MAX_FEATURES:
        issues.append(Issue("feature_limit", "2.11, 3.6, 7.3.11", f"This map has {len(visible)} visible elements. Reduce it to {MAX_FEATURES} or fewer, or separate it into multiple maps."))
    raw: list[tuple[Element, BaseGeometry]] = []
    for element in visible[:MAX_FEATURES]:
        try:
            geometry = element_geometry(element)
            if geometry.is_empty or not geometry.is_valid:
                raise ValueError("Invalid geometry")
            raw.append((element, geometry))
        except (ValueError, TypeError):
            issues.append(Issue("invalid_geometry", "2.8, 10.2", "The feature has invalid geometry. Correct or hide it before production export.", [element.id]))
    pixel_geometries = [affinity.scale(geometry, rep.source.width, rep.source.height, origin=(0, 0)) for _, geometry in raw]
    bounds = unary_union(pixel_geometries).bounds if pixel_geometries else (0, 0, 1, 1)
    if viewport is not None:
        bounds = (viewport[0] * rep.source.width, viewport[1] * rep.source.height, viewport[2] * rep.source.width, viewport[3] * rep.source.height)
    label_count = sum(bool(element.label) for element, _ in raw)
    categories = {(element.category, element.geometry.type) for element, _ in raw if element.category != "labels"}
    estimated_key_height = (4 + len(categories) * 2 + label_count * 2) * LINE_PITCH_MM
    try:
        category_rows = sum(2 if category == "regions" and geometry_type == "polygon" else 1 for category, geometry_type in categories)
        name_rows = sum(len(wrap_braille(translator.translate(element.label), max(columns - len(translator.translate(number_keys[element.id], "en-ueb-g1.ctb")) - 1, 1))) for element, _ in raw if element.label) if output.label_mode != "full" else 0
        estimated_key_height = (5 + category_rows + name_rows) * LINE_PITCH_MM
    except BrailleError:
        pass
    inline_key = output.legend and content_height - title_height - estimated_key_height >= 100
    map_top = MARGIN_MM + title_height + (estimated_key_height if inline_key and output.key_position == "before" else 0)
    map_height = content_height - title_height - (estimated_key_height if inline_key else 0)
    frame_values = (MARGIN_MM, map_top, content_width, max(20, map_height))
    frame = box(frame_values[0], frame_values[1], frame_values[0] + frame_values[2], frame_values[1] + frame_values[3])
    features: list[Feature] = []
    for element, geometry in raw:
        mapped = transform_geometry(geometry, rep, bounds, frame_values)
        kind = "label" if element.category == "labels" else "point" if element.geometry.type == "point" else "area" if element.geometry.type == "polygon" and element.category == "regions" else "line"
        if kind == "line" and isinstance(mapped, Polygon):
            mapped = mapped.boundary
        pattern = POINT_STYLES.get(element.category, "square") if kind == "point" else (element.style.texture if element.style.texture != "none" else output.texture) if kind == "area" else LINE_STYLES.get(element.category, "solid")
        ink = point_shape(mapped, pattern) if kind == "point" else mapped.boundary.buffer(LINE_WIDTH_MM / 2) if kind == "area" else mapped.buffer(LINE_WIDTH_MM / 2)
        features.append(Feature(element, mapped, ink, kind, pattern))
        if kind == "line" and mapped.length < PRIMARY_LINE_MIN_MM:
            issues.append(Issue("primary_line_too_short", "3.4.3.2", "A primary line is shorter than 12.7 mm. Enlarge the page or simplify the content.", [element.id], measured_mm=round(mapped.length, 3), required_mm=PRIMARY_LINE_MIN_MM))
        if kind == "area" and mapped.area < AREA_MIN_MM2:
            issues.append(Issue("area_too_small", "3.4.3.1", "An area is below the conservative 1/4-square-inch threshold. Enlarge it or split the map. The manual's metric equivalent is inconsistent; qualified review is required.", [element.id]))
    point_clearances = [feature.ink.buffer(gap + LINE_WIDTH_MM / 2) for feature in features if feature.kind == "point"]
    clearances = unary_union(point_clearances) if point_clearances else GeometryCollection()
    cut_count = 0
    for feature in features:
        if feature.kind == "line" and not clearances.is_empty:
            if feature.geometry.intersects(clearances):
                cut_count += 1
            feature.geometry = feature.geometry.difference(clearances)
            feature.ink = feature.geometry.buffer(LINE_WIDTH_MM / 2)
            fragments = list(feature.geometry.geoms) if hasattr(feature.geometry, "geoms") else [feature.geometry]
            if any(part.is_empty or part.length < PRIMARY_LINE_MIN_MM for part in fragments):
                issues.append(Issue("line_fragment_too_short", "3.4.3.2", "A line fragment left around a point symbol is shorter than 12.7 mm. Simplify the geometry or separate the features.", [feature.element.id], required_mm=PRIMARY_LINE_MIN_MM))
    crossing_count = 0
    for first, second in combinations(features, 2):
        if first.kind == "label" or second.kind == "label":
            continue
        if first.kind == "line" and second.kind == "line" and first.geometry.intersects(second.geometry):
            crossing_count += 1
            continue
        distance = first.ink.distance(second.ink)
        if distance < gap - 1e-4:
            issues.append(Issue("feature_clearance", "2.11, 3.4.3.11", "Features are too close for the selected tactile clearance. Enlarge the page, hide detail, or split the map.", [first.element.id, second.element.id], measured_mm=round(distance, 3), required_mm=gap))
            if len(issues) >= 80:
                break
    labels: list[PlacedLabel] = []
    numeric_entries: dict[str, KeyEntry] = {}
    try:
        entries = key_entries(features, translator, max(columns, 12))
    except BrailleError as error:
        entries = []
        issues.append(Issue("key_translation", "5.7, 5.8", str(error)))
    for feature in sorted(features, key=lambda feature: (-feature.element.priority, feature.element.id)):
        text = feature.element.label
        if not text:
            continue
        try:
            cells = translator.translate(text)
            placement = try_place(feature, cells, features, labels, frame, gap) if output.label_mode != "numbered" else None
            keyed = False
            key = ""
            if placement is None and output.label_mode != "full":
                key = number_keys[feature.element.id]
                cells = translator.translate(key, "en-ueb-g1.ctb")
                placement = try_place(feature, cells, features, labels, frame, gap)
                keyed = True
            if placement is None:
                issues.append(Issue("label_clearance", "3.4.3.12, 3.4.3.13, 5.10", "No collision-free braille label position was found. Enlarge the page, shorten the label, or hide nearby detail.", [feature.element.id]))
                continue
            labels.append(PlacedLabel(feature.element.id, text, cells, placement, keyed, key))
            if keyed:
                explanation = translator.translate(text)
                lines = wrap_braille(explanation, max(columns - len(cells) - 1, 1))
                numeric_entries[key] = KeyEntry("numeric", text, explanation, key=key, lines=lines, height=len(lines) * LINE_PITCH_MM)
        except BrailleError as error:
            issues.append(Issue("label_translation", "2.3, 5.8, 5.10", str(error), [feature.element.id]))
    entries += [numeric_entries[key] for key in sorted(numeric_entries, key=int)]
    if entries and not output.legend:
        issues.append(Issue("key_required", "5.7, 5.8", "The key must be enabled for BANA production output."))
    if entries and output.legend and output.key_position == "below":
        issues.append(Issue("key_placement_review", "5.8.3", "The key is placed below or after the map as selected. BANA prescribes the key before the graphic; choose Before map (BANA) for that production order.", severity="review"))
    for kind in ("area", "line", "point"):
        if len({feature.pattern for feature in features if feature.kind == kind}) > 5:
            issues.append(Issue("too_many_symbol_styles", "7.3.11", "More than five styles require separation into simpler maps."))
    if crossing_count:
        issues.append(Issue("crossings_review", "2.11, 10.2", f"Review {crossing_count} line crossing(s) on a physical sample. Automatic geometry cannot determine whether every crossing is an intentional connection.", severity="review"))
    if cut_count:
        issues.append(Issue("connection_gaps_review", "2.11, 10.2", "Line strokes are interrupted around point symbols for clearance. Verify connections against the source.", severity="review"))
    blank_label_regions = unary_union([label.bounds.buffer(gap) for label in labels] + point_clearances) if labels or point_clearances else GeometryCollection()
    map_content = [title]
    for feature in features:
        if feature.kind == "label":
            continue
        geometry = feature.geometry
        fill = "none"
        if feature.kind == "area" and feature.pattern != "none":
            texture_area = geometry.buffer(-LINE_WIDTH_MM / 2).difference(blank_label_regions)
            map_content.append(f'<path d="{geometry_path(texture_area)}" fill="url(#tactile-{feature.pattern})" fill-rule="evenodd" stroke="none"/>')
        if feature.kind == "point":
            drawing = point_svg(geometry.x, geometry.y, feature.pattern)
        else:
            drawing = f'<path d="{geometry_path(geometry)}" fill="{fill}" {stroke_attributes(feature.pattern if feature.kind == "line" else "solid")}/>'
        map_content.append(f'<g id="{escape(feature.element.id, quote=True)}"><title>{escape(feature.element.label or feature.element.category)}</title>{drawing}</g>')
    for label in labels:
        x, y, _, _ = label.bounds.bounds
        map_content.append(draw_braille(label.cells, x, y, label.text, "en-ueb-g1.ctb" if label.keyed else translator.table))
    pages: list[Page] = []
    key_top: float | None = None
    if output.legend and entries:
        if inline_key:
            key_top = map_top + map_height + LINE_PITCH_MM if output.key_position == "below" else MARGIN_MM + title_height
            key_svg, key_end = draw_key(entries, translator, MARGIN_MM, key_top, max(columns, 12))
            key_limit = MARGIN_MM + content_height if output.key_position == "below" else map_top - LINE_PITCH_MM
            if key_end > key_limit + 1e-4:
                issues.append(Issue("key_overflow", "5.8.3, 5.8.4", "The same-page key exceeds its reserved space. Reduce detail or enlarge the page."))
            map_content.append(key_svg)
        else:
            if output.key_position == "below" and numeric_entries:
                issues.append(Issue("key_continues_on_page", "5.8.3", "The full key needs a separate page for this view. It follows the map; use fewer elements per view or allow more views to keep the key below the graphic on one page.", severity="review"))
            capacity = max(LINE_PITCH_MM, content_height - title_height - 4 * LINE_PITCH_MM)
            batches: list[list[KeyEntry]] = [[]]
            consumed = 0.0
            for entry in entries:
                if entry.height > capacity:
                    issues.append(Issue("key_entry_overflow", "5.8.4", "A key explanation is too long for the page. Shorten the corresponding label."))
                if consumed + entry.height > capacity and batches[-1]:
                    batches.append([])
                    consumed = 0
                batches[-1].append(entry)
                consumed += entry.height
            if len(batches) > 8:
                issues.append(Issue("too_many_key_pages", "7.3.11", "This map requires too many key pages. Split it into simpler maps."))
            for index, batch in enumerate(batches[:8]):
                key_svg, key_end = draw_key(batch, translator, MARGIN_MM, MARGIN_MM + title_height, max(columns, 12))
                if key_end > MARGIN_MM + content_height:
                    issues.append(Issue("key_bounds", "3.4.2, 5.8.4", "A key page exceeds the braille page grid."))
                pages.append(Page(f"key-{index + 1}", svg_page(rep, title + key_svg, "Key", "Braille key. Arrange facing the associated map according to the validation report."), "left" if len(batches) == 1 and output.key_position == "before" else "right"))
    visible_categories = sorted({feature.element.category for feature in features})
    description = f"Visible map categories: {', '.join(visible_categories)}. {len(labels)} braille labels. Source interpretation: {rep.description}"
    map_page = Page("map", svg_page(rep, "".join(map_content), "Map", description), "left" if pages and output.key_position == "below" else "right" if len(pages) == 1 else "left" if len(pages) > 1 else "either")
    if len(pages) > 1 or output.key_position == "below" and pages:
        pages.insert(0, map_page)
        map_index = 0
    else:
        map_index = len(pages)
        pages.append(map_page)
    errors = [issue for issue in issues if issue.severity == "error"]
    report = {
        "standard": "BANA Guidelines and Standards for Tactile Graphics, 2022",
        "source_url": STANDARD_URL,
        "source_sha256": STANDARD_SHA256,
        "braille_size_source": BRAILLE_SIZE_URL,
        "status": "blocked" if errors else "human_review_required",
        "automated_checks_passed": not errors,
        "certified": False,
        "requires_human_review": True,
        "production_medium": "microcapsule preparation; device calibration required",
        "braille": {"translator": "Liblouis", "table": translator.table, "key_table": "en-ueb-g1.ctb", "dot_diameter_mm": DOT_DIAMETER_MM, "dot_pitch_mm": DOT_PITCH_MM, "cell_pitch_mm": CELL_PITCH_MM, "line_pitch_mm": LINE_PITCH_MM, "physical_dot_height_verified": False},
        "page": {"width_mm": width, "height_mm": height, "columns": columns, "rows": rows, "margin_mm": MARGIN_MM, "minimum_clearance_mm": gap, "point_symbol_diameter_mm": SYMBOL_DIAMETER_MM},
        "scope": ["physical units", "six-dot UEB braille", "minimum primary line length", "conservative area size", "point symbol dimensions", "point-to-feature clearance", "label-to-feature and label-to-label clearance", "label texture knockout", "key dimensions, order, matching symbols and braille formatting", "page grid and bounds"],
        "issues": [issue.__dict__ for issue in issues],
        "manual_checks": MANUAL_CHECKS,
        "pages": [{"index": index, "role": page.role, "recommended_side": page.recommended_side} for index, page in enumerate(pages)],
        "map_page_index": map_index,
        "map_frame_mm": list(frame_values),
        "source_bounds": list(viewport) if viewport is not None else [bounds[0] / rep.source.width, bounds[1] / rep.source.height, bounds[2] / rep.source.width, bounds[3] / rep.source.height],
        "key_position": output.key_position,
        "key_inline": inline_key and bool(entries),
        "key_top_mm": key_top,
        "rendered_element_ids": sorted({feature.element.id for feature in features if feature.kind != "label" and not feature.geometry.is_empty} | {label.element_id for label in labels}),
        "labels": [{"element_id": label.element_id, "text": label.text, "braille": label.cells, "key": label.key, "bounds_mm": list(label.bounds.bounds)} for label in labels],
        "print_instructions": "Print at 100% actual size without fit-to-page. Key and map must be assembled on the facing sides specified in pages. For multiple key pages, use map-left/key-right pairs, adding blank reverse pages as needed. Confirm braille height, material, orientation, source accuracy, and reader suitability on a physical proof.",
    }
    return TactileDocument(pages, map_index, report)


def prepare_document(rep: Representation) -> TactileDocument:
    from app.exporters.pagination import prepare_adaptive_document
    return prepare_adaptive_document(rep)
