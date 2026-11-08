import json
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import pytest
from pypdf import PdfReader
from PIL import Image
from shapely.geometry import box
from app.exporters.braille import BrailleTranslator, BrailleError, draw_braille
from app.exporters.render import exporters
from app.exporters.english_preview import back_translate, render_english_preview
from app.exporters.tactile import TactileValidationError, prepare_document
from app.schemas import Element, Geometry, Representation, Source
from app.storage.local import LocalTemporaryStorageProvider


def map_representation():
    return Representation(project_id="7b1e35aa-a340-44c4-b12a-96e718340c23", source=Source(filename="map.png", width=800, height=600, mime_type="image/png"), elements=[
        Element(id="border", category="borders", geometry=Geometry(type="polyline", coordinates=[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9], [0.1, 0.1]]), confidence=1, provenance="synthetic"),
        Element(id="city", category="cities", label="Oak Town", geometry=Geometry(type="point", coordinates=[[0.5, 0.5]]), confidence=1, provenance="synthetic"),
    ])


def crowded_representation():
    rep = map_representation()
    rep.elements = [rep.elements[0]]
    for index in range(16):
        rep.elements.append(Element(id=f"city-{index:02}", category="cities", label=f"North Springfield {index + 1}", geometry=Geometry(type="point", coordinates=[[0.4 + index % 4 * 0.014, 0.4 + index // 4 * 0.014]]), confidence=1, provenance="synthetic"))
    return rep


def test_crowded_maps_expand_to_complete_views_with_full_names():
    rep = crowded_representation()
    original = rep.model_dump_json()
    document = prepare_document(rep)
    report = document.report
    assert report["automated_checks_passed"], report["issues"]
    assert report["layout"]["automatically_split"]
    assert 1 < report["layout"]["map_views"] <= 16
    assert report["layout"]["coverage_complete"]
    assert report["layout"]["covered_elements"] == 17
    assert all(not label["key"] for label in report["labels"])
    assert {label["text"] for label in report["labels"]} == {element.label for element in rep.elements if element.label}
    assigned = [element_id for panel in report["panels"] for element_id in panel["element_ids"]]
    assert sorted(assigned) == sorted(element.id for element in rep.elements)
    assert max(panel["zoom"] for panel in report["panels"]) > 2
    for panel in report["panels"]:
        window = box(*panel["source_bounds"])
        for element in rep.elements:
            if element.id in panel["element_ids"]:
                from app.exporters.tactile import element_geometry
                assert window.buffer(1e-8).covers(element_geometry(element))
        page = ET.fromstring(document.pages[panel["map_page_index"]].svg)
        assert panel["automated_checks_passed"]
        for element_id in panel["element_ids"]:
            assert page.find(f".//{{http://www.w3.org/2000/svg}}g[@id='{element_id}']") is not None
    assert rep.model_dump_json() == original


def test_numbered_views_have_consistent_braille_references_and_all_pages_export():
    from app.exporters.render import render_svg
    rep = crowded_representation()
    rep.export_settings.label_mode = "numbered"
    document = prepare_document(rep)
    assert document.report["automated_checks_passed"], document.report["issues"]
    assert document.report["layout"]["automatically_split"]
    translator = BrailleTranslator("en-ueb-g1.ctb")
    references = {entry["element_id"]: entry for entry in document.report["numeric_key"]}
    assert len(references) == 16
    assert all(panel["key_inline"] for panel in document.report["panels"])
    for label in document.report["labels"]:
        assert label["key"] == references[label["element_id"]]["number"]
        assert label["braille"] == translator.translate(label["key"])
        assert label["braille"].startswith("⠼")
        assert references[label["element_id"]]["text"] == label["text"]
    for panel in document.report["panels"]:
        map_page = ET.fromstring(render_svg(rep, panel["map_page_index"], english=True))
        english_labels = {group.get("aria-label") for group in map_page.findall(".//{http://www.w3.org/2000/svg}g[@class='braille-english']")}
        for label in document.report["labels"]:
            if label["view_number"] == panel["number"]:
                assert label["key"] in english_labels
    pdf = PdfReader(BytesIO(exporters["pdf"].render(rep)))
    assert len(pdf.pages) == len(document.pages)
    with ZipFile(BytesIO(exporters["bundle"].render(rep))) as archive:
        assert len([name for name in archive.namelist() if name.endswith(".svg")]) == len(document.pages)
        assert json.loads(archive.read("validation.json"))["layout"]["coverage_complete"]
    selected = document.report["panels"][-1]["map_page_index"]
    assert exporters["svg"].render(rep, selected).decode() == document.pages[selected].svg


def test_reference_numbers_survive_visibility_changes_and_duplicate_ids_are_blocked():
    rep = crowded_representation()
    rep.export_settings.label_mode = "numbered"
    original = prepare_document(rep).report
    numbers = {entry["element_id"]: entry["number"] for entry in original["numeric_key"]}
    rep.elements[1].visible = False
    changed = prepare_document(rep).report
    assert all(numbers[entry["element_id"]] == entry["number"] for entry in changed["numeric_key"])
    rep.elements[2].id = rep.elements[3].id
    invalid = prepare_document(rep).report
    assert not invalid["automated_checks_passed"]
    assert not invalid["layout"]["coverage_complete"]
    assert any(issue["code"] == "duplicate_element_ids" for issue in invalid["issues"])


def test_below_map_key_is_geometrically_separate_and_deviation_is_explicit():
    rep = map_representation()
    rep.export_settings.label_mode = "numbered"
    document = prepare_document(rep)
    report = document.report
    assert report["key_inline"]
    frame = report["map_frame_mm"]
    assert report["key_top_mm"] >= frame[1] + frame[3] + 10
    assert any(issue["code"] == "key_placement_review" for issue in report["issues"])
    rep.export_settings.key_position = "before"
    report = prepare_document(rep).report
    assert not any(issue["code"] == "key_placement_review" for issue in report["issues"])


def test_view_limit_never_silently_drops_features_or_ignores_conflicts():
    rep = crowded_representation()
    rep.export_settings.max_map_views = 1
    document = prepare_document(rep)
    assert document.report["layout"]["limit_reached"]
    assert document.report["layout"]["covered_elements"] == 17
    assert not document.report["automated_checks_passed"]
    with pytest.raises(TactileValidationError):
        exporters["pdf"].render(rep)


def test_maps_above_single_view_feature_limit_retain_every_element():
    rep = map_representation()
    rep.elements = [Element(id=f"point-{index:03}", category="cities", geometry=Geometry(type="point", coordinates=[[0.05 + index % 10 * 0.09, 0.05 + index // 10 * 0.1]]), confidence=1, provenance="synthetic") for index in range(90)]
    document = prepare_document(rep)
    assert document.report["layout"]["automatically_split"]
    assert document.report["layout"]["covered_elements"] == 90
    assert document.report["layout"]["coverage_complete"]
    assert not any(issue["code"] in ("coverage_missing", "feature_limit") for issue in document.report["issues"])


def test_real_liblouis_translation_and_physical_dot_dimensions():
    translator = BrailleTranslator("en-ueb-g1.ctb")
    assert translator.translate("Map") == "⠠⠍⠁⠏"
    assert translator.translate("Oak Town") == "⠠⠕⠁⠅⠀⠠⠞⠕⠺⠝"
    root = ET.fromstring(draw_braille("⠿⠿", 0, 0, "Test"))
    dots = root.findall("circle")
    assert len(dots) == 12
    assert float(dots[0].attrib["r"]) * 2 == pytest.approx(1.4478, abs=0.0001)
    assert float(dots[1].attrib["cy"]) - float(dots[0].attrib["cy"]) == pytest.approx(2.3368, abs=0.0001)
    assert float(dots[6].attrib["cx"]) - float(dots[0].attrib["cx"]) == pytest.approx(6.223, abs=0.0001)


def test_english_preview_back_translates_real_cells_with_position_mapping():
    text, runs = back_translate("⠠⠍⠁⠏⠀⠼⠁⠃", "en-ueb-g1.ctb")
    assert text == "Map 12"
    assert "".join(run["text"] for run in runs) == text
    assert runs[0] == {"start": 0, "count": 2, "text": "M"}
    assert back_translate("⠮", "en-ueb-g2.ctb") == ("the", [{"start": 0, "count": 1, "text": "the"}])
    assert back_translate("⠈⠨⠜", "en-ueb-g1.ctb") == ("", [])


def test_english_preview_uses_braille_instead_of_source_label_and_preserves_map():
    from app.exporters.render import render_svg
    svg = f'<svg xmlns="http://www.w3.org/2000/svg">{draw_braille("⠕⠁", 10, 20, "Oak Town")}</svg>'
    english = ET.fromstring(render_english_preview(svg))
    group = english.find("{http://www.w3.org/2000/svg}g")
    assert group.attrib["aria-label"] == "oa"
    assert "".join(element.text or "" for element in group.findall("{http://www.w3.org/2000/svg}text")) == "oa"
    assert not group.findall("{http://www.w3.org/2000/svg}circle")
    rep = map_representation()
    original = ET.fromstring(render_svg(rep))
    translated = ET.fromstring(render_svg(rep, english=True))
    assert translated.attrib["viewBox"] == original.attrib["viewBox"]
    for element_id in ("border", "city"):
        selector = f".//{{http://www.w3.org/2000/svg}}g[@id='{element_id}']"
        assert ET.tostring(original.find(selector)) == ET.tostring(translated.find(selector))
    assert translated.findall(".//{http://www.w3.org/2000/svg}text")
    assert not ET.fromstring(exporters["svg"].render(rep)).findall(".//{http://www.w3.org/2000/svg}text")


def test_physical_layout_and_actual_multipage_files():
    rep = map_representation()
    original = rep.model_dump_json()
    document = prepare_document(rep)
    assert document.report["automated_checks_passed"], document.report["issues"]
    assert not document.report["certified"]
    assert document.report["requires_human_review"]
    assert document.report["page"]["rows"] == 25
    label = document.report["labels"][0]
    assert label["text"] == "Oak Town"
    bounds = box(*label["bounds_mm"])
    assert bounds.within(box(12.7, 12.7, 279.4, 266.7))
    svg = ET.fromstring(exporters["svg"].render(rep))
    assert svg.attrib["width"] == "292.1mm"
    assert svg.attrib["viewBox"] == "0 0 292.1 279.4"
    assert not svg.findall(".//{http://www.w3.org/2000/svg}text")
    city = svg.find(".//{http://www.w3.org/2000/svg}g[@id='city']/{http://www.w3.org/2000/svg}circle")
    assert city is not None
    assert float(city.attrib["r"]) * 2 + float(city.attrib["stroke-width"]) >= 6
    city_x, city_y = float(city.attrib["cx"]), float(city.attrib["cy"])
    from shapely.geometry import Point
    assert bounds.distance(Point(city_x, city_y).buffer(3.2)) >= 3.199
    pdf = PdfReader(BytesIO(exporters["pdf"].render(rep)))
    assert len(pdf.pages) == len(document.pages)
    assert float(pdf.pages[0].mediabox.width) * 25.4 / 72 == pytest.approx(292.1, abs=0.001)
    with Image.open(BytesIO(exporters["png"].render(rep))) as image:
        assert image.format == "PNG"
        assert image.width == 1800
        assert image.width / image.info["dpi"][0] * 25.4 == pytest.approx(292.1, abs=0.05)
    with ZipFile(BytesIO(exporters["bundle"].render(rep))) as archive:
        report = json.loads(archive.read("validation.json"))
        assert len([name for name in archive.namelist() if name.endswith(".svg")]) == len(report["pages"])
        assert "100%" in archive.read("print-instructions.txt").decode()
        assert len(PdfReader(BytesIO(archive.read("tactile-map.pdf"))).pages) == len(report["pages"])
    assert rep.model_dump_json() == original


def test_colliding_symbols_block_production_but_leave_draft_available():
    from app.exporters.render import render_svg
    rep = map_representation()
    rep.elements.append(rep.elements[1].model_copy(update={"id": "overlapping-city"}))
    document = prepare_document(rep)
    assert document.report["status"] == "blocked"
    assert any(issue["code"] == "feature_clearance" for issue in document.report["issues"])
    assert ET.fromstring(render_svg(rep)).tag.endswith("svg")
    for exporter in exporters.values():
        with pytest.raises(TactileValidationError):
            exporter.render(rep)


def test_numbered_braille_keys_preserve_separate_key_pages():
    rep = map_representation()
    rep.elements[1].label = "Oak Town Northern Educational District"
    rep.export_settings.page_height_mm = 200
    rep.export_settings.key_position = "before"
    rep.export_settings.label_mode = "numbered"
    document = prepare_document(rep)
    assert document.report["automated_checks_passed"], document.report["issues"]
    assert [page.role for page in document.pages] == ["key-1", "map"]
    assert document.map_index == 1
    label = document.report["labels"][0]
    assert 2 <= len(label["braille"]) <= 3
    assert any((ord(cell) - 0x2800) & 0b100100 for cell in label["braille"])
    assert label["key"] == "1"
    assert label["braille"].startswith("⠼")
    pdf = PdfReader(BytesIO(exporters["pdf"].render(rep)))
    assert len(pdf.pages) == 2
    with ZipFile(BytesIO(exporters["bundle"].render(rep))) as archive:
        assert "01-key-1.svg" in archive.namelist()
        assert "02-map.svg" in archive.namelist()
        root = ET.fromstring(archive.read("01-key-1.svg"))
        city = root.find(".//{http://www.w3.org/2000/svg}circle[@stroke='black']")
        assert city is not None
        assert float(city.attrib["r"]) * 2 + float(city.attrib["stroke-width"]) == pytest.approx(6.4)


def test_missing_braille_engine_fails_closed(monkeypatch):
    import app.exporters.braille as module
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("Missing executable")
    monkeypatch.setattr(module.subprocess, "run", unavailable)
    with pytest.raises(BrailleError):
        BrailleTranslator("en-ueb-g1.ctb").translate("Map")
    document = prepare_document(map_representation())
    assert not document.report["automated_checks_passed"]
    assert any(issue["code"] == "braille_translation" for issue in document.report["issues"])


def test_new_revision_invalidates_exports_preserving_original(tmp_path):
    rep = map_representation()
    storage = LocalTemporaryStorageProvider(tmp_path)
    project = storage.project_path(rep.project_id)
    project.mkdir()
    (project / "original.png").write_bytes(b"original")
    (project / "export.pdf").write_bytes(b"stale")
    storage.write(rep.project_id, "representation.json", rep.model_dump())
    assert not (project / "export.pdf").exists()
    assert (project / "original.png").read_bytes() == b"original"
