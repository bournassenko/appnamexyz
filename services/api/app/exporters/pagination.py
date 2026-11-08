from dataclasses import dataclass
from typing import Any
from shapely.ops import unary_union
from app.exporters.braille import BrailleError, BrailleTranslator, CELL_PITCH_MM, braille_width
from app.exporters.tactile import Page, TactileDocument, element_geometry, prepare_single_document
from app.schemas import Element, Representation

SPATIAL_ERRORS = {"feature_limit", "feature_clearance", "label_clearance", "primary_line_too_short", "line_fragment_too_short", "area_too_small", "too_many_key_pages", "key_overflow", "key_entry_overflow"}


@dataclass
class View:
    elements: list[Element]
    depth: int
    bounds: tuple[float, float, float, float]


def source_window(elements: list[Element]) -> tuple[float, float, float, float]:
    bounds = unary_union([element_geometry(element) for element in elements]).bounds
    left, top, right, bottom = bounds
    width, height = max(right - left, 0.12) * 1.16, max(bottom - top, 0.12) * 1.16
    center_x, center_y = (left + right) / 2, (top + bottom) / 2
    width, height = min(width, 1), min(height, 1)
    x, y = max(0, min(center_x - width / 2, 1 - width)), max(0, min(center_y - height / 2, 1 - height))
    return x, y, x + width, y + height


def needs_detail(document: TactileDocument, rep: Representation, translator: BrailleTranslator) -> bool:
    if any(issue["severity"] == "error" and issue["code"] in SPATIAL_ERRORS for issue in document.report["issues"]):
        return True
    if rep.export_settings.key_position == "below" and any(label["key"] for label in document.report["labels"]) and not document.report["key_inline"]:
        return True
    if rep.export_settings.label_mode != "auto":
        return False
    width = document.report["page"]["columns"] * CELL_PITCH_MM
    for label in document.report["labels"]:
        if label["key"]:
            try:
                if braille_width(translator.translate(label["text"])) <= width:
                    return True
            except BrailleError:
                return False
    return False


def partition(elements: list[Element], rep: Representation) -> tuple[list[Element], list[Element]]:
    anchors = [(element, element_geometry(element).centroid) for element in elements]
    span_x = (max(point.x for _, point in anchors) - min(point.x for _, point in anchors)) * rep.source.width
    span_y = (max(point.y for _, point in anchors) - min(point.y for _, point in anchors)) * rep.source.height
    ordered = sorted(anchors, key=lambda pair: (pair[1].x if span_x >= span_y else pair[1].y, pair[0].id))
    middle = len(ordered) // 2
    return [element for element, _ in ordered[:middle]], [element for element, _ in ordered[middle:]]


def view_representation(rep: Representation, elements: list[Element], number: int | None = None) -> Representation:
    view = rep.model_copy(deep=True)
    view.elements = [element.model_copy(deep=True) for element in elements]
    if number is not None:
        view.export_settings.tactile_title = f"{rep.export_settings.tactile_title} view {number}"
    return view


def physical_scale(report: dict[str, Any], rep: Representation) -> float:
    bounds = report["source_bounds"]
    frame = report["map_frame_mm"]
    return min((frame[2] - 7.8) / max((bounds[2] - bounds[0]) * rep.source.width, 1), (frame[3] - 7.8) / max((bounds[3] - bounds[1]) * rep.source.height, 1))


def prepare_adaptive_document(rep: Representation) -> TactileDocument:
    translator = BrailleTranslator(rep.export_settings.braille_table)
    numbers = {element.id: str(index + 1) for index, element in enumerate(sorted((element for element in rep.elements if element.label), key=lambda element: element.id))}
    initial = prepare_single_document(rep, translator=translator, number_keys=numbers)
    visible = [element for element in rep.elements if element.visible]
    forbidden = {issue["code"] for issue in initial.report["issues"] if issue["severity"] == "error"} - SPATIAL_ERRORS
    points = [element for element in visible if element.geometry.type == "point" and element.category != "labels"]
    coincident = len({tuple(element.geometry.coordinates[0]) for element in points}) != len(points)
    crowded = needs_detail(initial, rep, translator)
    split = rep.export_settings.split_crowded_maps and crowded and len(visible) > 1 and not forbidden and not coincident
    root_scale = physical_scale(initial.report, rep)
    accepted: list[View] = []
    queue = [View(visible, 0, tuple(initial.report["source_bounds"]))]
    if split:
        while queue:
            current = queue.pop(0)
            candidate = view_representation(rep, current.elements)
            document = prepare_single_document(candidate, translator, current.bounds, numbers)
            room = len(accepted) + len(queue) + 1 < rep.export_settings.max_map_views
            if room and current.depth < 8 and len(current.elements) > 1 and needs_detail(document, candidate, translator):
                first, second = partition(current.elements, rep)
                queue[:0] = [View(first, current.depth + 1, source_window(first)), View(second, current.depth + 1, source_window(second))]
            else:
                accepted.append(current)
    else:
        accepted = queue
    multiple = len(accepted) > 1
    pages: list[Page] = []
    panels: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    page_reports: list[dict[str, Any]] = []
    covered: set[str] = set()
    unresolved = 0
    primary_report = initial.report
    for index, view in enumerate(accepted):
        document = prepare_single_document(view_representation(rep, view.elements, index + 1), translator, view.bounds, numbers) if multiple else initial
        unresolved += int(needs_detail(document, rep, translator))
        if index == 0:
            primary_report = document.report
        offset = len(pages)
        panel = {"number": index + 1, "name": f"View {index + 1}" if multiple else "Map", "source_bounds": document.report["source_bounds"], "zoom": round(physical_scale(document.report, rep) / max(root_scale, 1e-9), 2), "element_ids": [element.id for element in view.elements], "map_page_index": offset + document.map_index, "key_page_indices": [offset + number for number, page in enumerate(document.pages) if page.role != "map"], "full_labels": sum(not label["key"] for label in document.report["labels"]), "numbered_labels": sum(bool(label["key"]) for label in document.report["labels"]), "automated_checks_passed": document.report["automated_checks_passed"], "key_inline": document.report["key_inline"], "key_top_mm": document.report["key_top_mm"], "map_frame_mm": document.report["map_frame_mm"]}
        panels.append(panel)
        covered.update(set(panel["element_ids"]) & set(document.report["rendered_element_ids"]))
        for local_index, page in enumerate(document.pages):
            role = f"view-{index + 1}-{page.role}" if multiple else page.role
            pages.append(Page(role, page.svg, page.recommended_side))
            page_reports.append({"index": offset + local_index, "role": role, "name": panel["name"] + (" — map" if local_index == document.map_index else " — " + page.role.replace("-", " ")), "recommended_side": page.recommended_side, "view_number": index + 1, "source_bounds": panel["source_bounds"], "zoom": panel["zoom"], "kind": "map" if local_index == document.map_index else "key"})
        issues.extend([{**issue, "view_number": index + 1} for issue in document.report["issues"]])
        labels.extend([{**label, "view_number": index + 1} for label in document.report["labels"]])
    missing = sorted({element.id for element in visible} - covered)
    if missing:
        issues.append({"code": "coverage_missing", "rule": "2.8", "message": "Some visible elements are missing from the generated views.", "element_ids": missing, "severity": "error", "measured_mm": None, "required_mm": None})
    if multiple:
        issues.append({"code": "multiple_views_review", "rule": "2.8, 7.3, 10.2", "message": "Every visible element is assigned to a source window without clipping its source geometry. Compare views with the original to verify relationships between features assigned to different views and any tactile clearance gaps.", "element_ids": [], "severity": "review", "measured_mm": None, "required_mm": None})
    grouped: dict[tuple, dict] = {}
    for issue in issues:
        key = issue["code"], issue["message"], tuple(issue["element_ids"]), issue["severity"]
        if key not in grouped:
            grouped[key] = {**issue, "view_numbers": []}
        if "view_number" in issue:
            grouped[key]["view_numbers"].append(issue["view_number"])
    used_numbers = {label["key"] for label in labels if label["key"]}
    complete = not missing and len(covered) == len(visible)
    report = {**primary_report, "pages": page_reports, "map_page_index": panels[0]["map_page_index"], "panels": panels, "labels": labels, "issues": list(grouped.values()), "layout": {"crowded": crowded, "automatically_split": multiple, "map_views": len(panels), "maximum_views": rep.export_settings.max_map_views, "limit_reached": unresolved > 0 and len(panels) >= rep.export_settings.max_map_views, "unresolved_views": unresolved, "coverage_complete": complete, "visible_elements": len(visible), "covered_elements": len(covered), "numbering": "stable element references across views", "original_map_bounds": initial.report["source_bounds"]}, "numeric_key": [{"number": number, "element_id": element.id, "text": element.label} for element in visible if (number := numbers.get(element.id)) in used_numbers]}
    report["rendered_element_ids"] = sorted(covered)
    report["scope"] = list(report["scope"]) + ["visible element coverage across views", "numeric braille references", "source window and zoom metadata"]
    report["automated_checks_passed"] = not any(issue["severity"] == "error" for issue in issues)
    report["status"] = "human_review_required" if report["automated_checks_passed"] else "blocked"
    if multiple:
        report["print_instructions"] += " Keep the numbered views together. View bounds and zoom ratios are included in the validation report; do not rescale braille to join the maps."
    return TactileDocument(pages, report["map_page_index"], report)
