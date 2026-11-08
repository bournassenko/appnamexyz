import math
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring, unary_union
from app.exporters.tactile import TactileDocument, prepare_document
from app.production.models import ProductionOptions
from app.schemas import Representation

MAX_DOTS = 6000
MAX_GRAPHIC_PARTS = 12000
MAX_GRAPHIC_VERTICES = 180000


@dataclass
class ProductionScene:
    document: TactileDocument
    graphics: BaseGeometry
    braille_dots: list[list[float]]
    graphic_dots: list[list[float]]
    options: ProductionOptions

    def to_dict(self) -> dict:
        page = self.document.report["page"]
        return {"units": "mm", "width_mm": page["width_mm"], "height_mm": page["height_mm"], "mode": self.options.mode, "base_thickness_mm": self.options.base, "relief_height_mm": self.options.height, "braille_height_mm": self.options.braille_height_mm, "graphics": mapping(self.graphics), "braille_dots": self.braille_dots, "graphic_dots": self.graphic_dots, "braille_dot_count": len(self.braille_dots), "graphics_dot_pitch_mm": 2.5 if self.options.mode == "emboss" else None, "layout_passed": self.document.report["automated_checks_passed"], "approximate": True, "hardware_validated": False, "brf_scope": "title and numbered text key only", "description": "Derived from the unchanged finished map, including its complete numbered key. Material behavior and device output are illustrative; physical production requires calibration."}


def path_runs(data: str) -> list[tuple[list[tuple[float, float]], bool]]:
    if re.search(r"[A-DF-KN-Ya-df-kn-y]", data):
        raise ValueError("The map contains an unsupported SVG path command for 3D conversion.")
    tokens = re.findall(r"[MLZmlz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", data)
    runs = []
    points: list[tuple[float, float]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "M":
            if points:
                runs.append((points, False))
            points = []
            index += 1
        elif token == "L":
            index += 1
        elif token == "Z":
            if points:
                runs.append((points, True))
            points = []
            index += 1
            continue
        elif token in ("m", "l", "z"):
            raise ValueError("Relative paths are not supported in the finished-map 3D adapter.")
        if index < len(tokens):
            if index + 1 >= len(tokens):
                raise ValueError("The finished SVG contains an incomplete coordinate.")
            points.append((float(tokens[index]), float(tokens[index + 1])))
            index += 2
    if points:
        runs.append((points, False))
    return runs


def polygons(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [polygon for part in geometry.geoms for polygon in polygons(part)]
    return []


def texture_geometry(area: BaseGeometry, pattern: str) -> BaseGeometry:
    left, top, right, bottom = area.bounds
    parts: list[BaseGeometry] = []
    if pattern == "tactile-dots":
        for row in range(math.floor(top / 5) - 1, math.ceil(bottom / 5) + 1):
            for column in range(math.floor(left / 5) - 1, math.ceil(right / 5) + 1):
                dot = Point(column * 5 + 2.5, row * 5 + 2.5).buffer(0.6, quad_segs=6)
                if area.intersects(dot):
                    parts.append(dot.intersection(area))
                    if len(parts) > MAX_GRAPHIC_PARTS:
                        raise ValueError("This texture is too detailed for bounded 3D conversion. The original map exports remain available.")
    elif pattern == "tactile-diagonal":
        extent = right - left + bottom - top + 10
        for total in range(math.floor((left + top) / 5) - 2, math.ceil((right + bottom) / 5) + 2):
            middle = Point(left, total * 5 - left)
            line = LineString([(middle.x - extent, middle.y + extent), (middle.x + extent, middle.y - extent)])
            stroke = line.buffer(0.2, cap_style="flat").intersection(area)
            if not stroke.is_empty:
                parts.append(stroke)
    else:
        raise ValueError("The finished map uses an unsupported texture for 3D conversion.")
    return unary_union(parts) if parts else GeometryCollection()


def svg_geometry(svg: str) -> tuple[BaseGeometry, list[list[float]]]:
    root = ET.fromstring(svg)
    parts: list[BaseGeometry] = []
    dots: list[list[float]] = []
    def visit(element: ET.Element) -> None:
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in ("defs", "metadata", "title", "desc"):
            return
        if tag == "circle" and element.get("class") == "braille-dot":
            dots.append([float(element.attrib["cx"]), float(element.attrib["cy"]), float(element.attrib["r"])])
            if len(dots) > MAX_DOTS:
                raise ValueError("The map contains too many braille dots for this 3D preview limit. The unchanged SVG, PDF and PNG exports remain available.")
            return
        if tag == "path":
            runs = path_runs(element.get("d", ""))
            fill = element.get("fill", "none")
            if fill != "none":
                area: BaseGeometry = GeometryCollection()
                for points, closed in runs:
                    if closed and len(points) >= 3:
                        polygon = Polygon(points)
                        if not polygon.is_valid:
                            raise ValueError("The finished map contains invalid filled geometry.")
                        area = area.symmetric_difference(polygon)
                if fill.startswith("url(#"):
                    area = texture_geometry(area, fill[5:-1]) if not area.is_empty else area
                elif fill not in ("black", "#000", "#000000"):
                    raise ValueError("Unsupported filled geometry in the finished map.")
                parts.extend(polygons(area))
            if element.get("stroke") in ("black", "#000", "#000000"):
                width = float(element.get("stroke-width", "1"))
                dash = [float(value) for value in re.findall(r"[\d.]+", element.get("stroke-dasharray", ""))]
                for points, closed in runs:
                    if len(points) < 2:
                        continue
                    line = LineString([*points, points[0]] if closed and points[-1] != points[0] else points)
                    strokes: list[BaseGeometry] = []
                    if dash:
                        if len(dash) != 2 or sum(dash) <= 0:
                            raise ValueError("Unsupported dash pattern in the finished map.")
                        position = 0.0
                        while position < line.length:
                            strokes.append(substring(line, position, min(position + dash[0], line.length)).buffer(width / 2, quad_segs=6))
                            position += sum(dash)
                            if len(strokes) > MAX_GRAPHIC_PARTS:
                                raise ValueError("The line pattern exceeds the 3D conversion limit.")
                    else:
                        strokes.append(line.buffer(width / 2, quad_segs=6, cap_style="round", join_style="round"))
                    parts.extend(polygons(unary_union(strokes)))
        for child in element:
            visit(child)
    visit(root)
    geometry = unary_union(parts) if parts else GeometryCollection()
    count = sum(len(polygon.exterior.coords) + sum(len(ring.coords) for ring in polygon.interiors) for polygon in polygons(geometry))
    if count > MAX_GRAPHIC_VERTICES:
        raise ValueError("The map geometry exceeds the bounded 3D model complexity. Its existing exports are unchanged.")
    return geometry, dots


def emboss_dots(graphics: BaseGeometry) -> list[list[float]]:
    result = []
    occupied: set[tuple[int, int]] = set()
    for polygon in polygons(graphics):
        left, top, right, bottom = polygon.bounds
        buffered = polygon.buffer(0.8)
        for row in range(math.floor(top / 2.5), math.ceil(bottom / 2.5) + 1):
            for column in range(math.floor(left / 2.5), math.ceil(right / 2.5) + 1):
                key = column, row
                if key not in occupied and buffered.covers(Point(column * 2.5, row * 2.5)):
                    occupied.add(key)
                    result.append([column * 2.5, row * 2.5, 0.7])
                    if len(result) > 18000:
                        raise ValueError("This map exceeds the generic dot-embosser preview limit.")
    return result


def build_scene(rep: Representation, options: ProductionOptions) -> ProductionScene:
    if rep.export_settings.profile != "bana2022":
        raise ValueError("Production previews require the numbered braille output profile.")
    document = prepare_document(rep)
    graphics, dots = svg_geometry(document.pages[document.map_index].svg)
    graphics = graphics.intersection(box(0, 0, document.report["page"]["width_mm"], document.report["page"]["height_mm"]))
    dotted = emboss_dots(graphics) if options.mode == "emboss" and not graphics.is_empty else []
    return ProductionScene(document, graphics, dots, dotted, options)
