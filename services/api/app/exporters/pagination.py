from app.exporters.braille import BrailleTranslator
from app.exporters.tactile import TactileDocument, prepare_single_document
from app.schemas import Representation

SPATIAL_ERRORS = {"feature_clearance", "label_clearance", "primary_line_too_short", "line_fragment_too_short", "area_too_small"}
CATEGORY_NAMES = {"borders": "border", "rivers": "river", "roads": "road", "regions": "region", "cities": "city", "labels": "label", "landmarks": "landmark", "other": "feature"}


def prepare_adaptive_document(rep: Representation) -> TactileDocument:
    target = rep.model_copy(deep=True)
    target.export_settings.label_mode = "numbered"
    target.export_settings.legend = True
    target.export_settings.key_position = "below"
    target.export_settings.split_crowded_maps = False
    target.export_settings.max_map_views = 1
    for element in target.elements:
        if not element.label:
            element.label = f"Unnamed {CATEGORY_NAMES[element.category]}"
    numbers = {element.id: str(index + 1) for index, element in enumerate(sorted(target.elements, key=lambda element: element.id))}
    translator = BrailleTranslator(target.export_settings.braille_table)
    document: TactileDocument | None = None
    chosen_scale = 1.0
    for scale in (1.0, 1.5, 2.0, 3.0, 4.0):
        candidate = target.model_copy(deep=True)
        candidate.export_settings.page_width_mm = min(1200, rep.export_settings.page_width_mm * scale)
        candidate.export_settings.page_height_mm = min(1200, rep.export_settings.page_height_mm * scale)
        current = prepare_single_document(candidate, translator=translator, number_keys=numbers, continuous_key=True)
        document = current
        chosen_scale = scale
        errors = [issue for issue in current.report["issues"] if issue["severity"] == "error"]
        if not errors or any(issue["code"] not in SPATIAL_ERRORS for issue in errors):
            break
        points = [element for element in target.elements if element.visible and element.geometry.type == "point" and element.category != "labels"]
        if len({tuple(element.geometry.coordinates[0]) for element in points}) != len(points):
            break
    if document is None:
        raise ValueError("The numbered map could not be generated.")
    visible = [element for element in target.elements if element.visible]
    covered = set(document.report["rendered_element_ids"])
    complete = len(covered) == len(visible) and all(element.id in covered for element in visible)
    if not complete:
        document.report["issues"].append({"code": "coverage_missing", "rule": "2.8", "message": "Some elements could not be labeled safely on this sheet. Enlarge the map area or correct the conflicting elements.", "element_ids": [element.id for element in visible if element.id not in covered], "severity": "error", "measured_mm": None, "required_mm": None})
    for label in document.report["labels"]:
        label["view_number"] = 1
    panel = {"number": 1, "name": "Map", "source_bounds": document.report["source_bounds"], "zoom": chosen_scale, "element_ids": [element.id for element in visible], "map_page_index": 0, "key_page_indices": [], "full_labels": 0, "numbered_labels": len(document.report["labels"]), "automated_checks_passed": document.report["automated_checks_passed"], "key_inline": True, "key_top_mm": document.report["key_top_mm"], "map_frame_mm": document.report["map_frame_mm"]}
    document.report["pages"] = [{"index": 0, "role": "map", "name": "Map and numbered key", "recommended_side": "either", "view_number": 1, "source_bounds": panel["source_bounds"], "zoom": chosen_scale, "kind": "map"}]
    document.report["panels"] = [panel]
    document.report["map_page_index"] = 0
    document.report["layout"] = {"crowded": chosen_scale > 1, "automatically_split": False, "map_views": 1, "maximum_views": 1, "limit_reached": False, "coverage_complete": complete, "visible_elements": len(visible), "covered_elements": len(covered), "automatic_enlargement": chosen_scale, "numbering": "literary braille numbers for every visible element"}
    document.report["scope"] = [scope for scope in document.report["scope"] if scope not in ("point symbol dimensions", "point-to-feature clearance")] + ["number-to-feature and number-to-number clearance", "complete same-sheet numbered legend"]
    document.report["numeric_key"] = [{"number": numbers[element.id], "element_id": element.id, "text": element.label} for element in visible]
    document.report["automated_checks_passed"] = not any(issue["severity"] == "error" for issue in document.report["issues"])
    document.report["status"] = "human_review_required" if document.report["automated_checks_passed"] else "blocked"
    panel["automated_checks_passed"] = document.report["automated_checks_passed"]
    document.report["print_instructions"] = "One map with literary braille number labels and the complete contracted UEB key below it. Print at 100% actual size on media matching the sheet dimensions. The sheet may be larger than standard braille paper. Verify physical braille height, spacing, source accuracy, and tactile usability before production. Prefer vector SVG or PDF for printing."
    return document
