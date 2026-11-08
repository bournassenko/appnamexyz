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
from app.exporters.tactile import prepare_document
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


def test_existing_projects_migrate_to_one_numbered_map():
    from app.schemas import OutputSettings
    settings = OutputSettings.model_validate({"braille_table": "en-ueb-g1.ctb", "max_map_views": 32, "label_mode": "full", "split_crowded_maps": True, "key_position": "before"})
    assert settings.braille_table == "en-ueb-g2.ctb"
    assert settings.max_map_views == 1
    assert settings.label_mode == "numbered"
    assert not settings.split_crowded_maps
    assert settings.key_position == "below"


def test_dense_map_has_one_sheet_all_numeric_labels_and_complete_bottom_key():
    rep = crowded_representation()
    original = rep.model_dump_json()
    document = prepare_document(rep)
    report = document.report
    assert report["automated_checks_passed"], report["issues"]
    assert len(document.pages) == 1
    assert report["layout"]["map_views"] == 1
    assert not report["layout"]["automatically_split"]
    assert report["layout"]["coverage_complete"]
    assert len(report["labels"]) == len(rep.elements) == 17
    assert len(report["numeric_key"]) == 17
    assert report["layout"]["automatic_enlargement"] > 1
    translator = BrailleTranslator("en-ueb-g1.ctb")
    key = {entry["element_id"]: entry for entry in report["numeric_key"]}
    for label in report["labels"]:
        assert label["key"] == key[label["element_id"]]["number"]
        assert label["braille"] == translator.translate(label["key"])
        assert label["braille"].startswith("⠼")
        assert label["bounds_mm"][3] < report["key_top_mm"]
    frame = report["map_frame_mm"]
    assert report["key_top_mm"] >= frame[1] + frame[3] + 10
    assert {entry["text"] for entry in report["numeric_key"]} >= {element.label for element in rep.elements if element.label}
    assert "Unnamed border" in {entry["text"] for entry in report["numeric_key"]}
    root = ET.fromstring(document.pages[0].svg)
    for element in rep.elements:
        assert root.find(f".//{{http://www.w3.org/2000/svg}}g[@id='{element.id}']") is not None
    bounds = [box(*label["bounds_mm"]) for label in report["labels"]]
    for index, first in enumerate(bounds):
        assert all(first.distance(second) >= 3.199 for second in bounds[index + 1:])
    assert rep.model_dump_json() == original


def test_reference_numbers_survive_visibility_changes_and_duplicate_ids_are_blocked():
    rep = crowded_representation()
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
    for element_id in ("border",):
        selector = f".//{{http://www.w3.org/2000/svg}}g[@id='{element_id}']"
        assert ET.tostring(original.find(selector)) == ET.tostring(translated.find(selector))
    assert translated.findall(".//{http://www.w3.org/2000/svg}text")
    assert not ET.fromstring(exporters["svg"].render(rep)).findall(".//{http://www.w3.org/2000/svg}text")


def test_single_sheet_physical_dimensions_and_actual_exports():
    rep = map_representation()
    original = rep.model_dump_json()
    document = prepare_document(rep)
    assert document.report["automated_checks_passed"], document.report["issues"]
    assert not document.report["certified"]
    assert document.report["requires_human_review"]
    assert document.report["page"]["rows"] == 25
    label = next(label for label in document.report["labels"] if label["element_id"] == "city")
    assert label["text"] == "Oak Town"
    bounds = box(*label["bounds_mm"])
    assert bounds.within(box(12.7, 12.7, 279.4, 266.7))
    svg = ET.fromstring(exporters["svg"].render(rep))
    assert svg.attrib["width"] == "292.1mm"
    assert svg.attrib["viewBox"] == f"0 0 292.1 {document.report['page']['height_mm']}"
    assert not svg.findall(".//{http://www.w3.org/2000/svg}text")
    city = svg.find(".//{http://www.w3.org/2000/svg}g[@id='city']")
    assert city is not None
    assert not city.findall("{http://www.w3.org/2000/svg}circle")
    assert city.find("{http://www.w3.org/2000/svg}g").attrib["data-braille"].startswith("⠼")
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


def test_colliding_symbols_allow_all_exports_without_changing_map():
    from app.exporters.render import render_svg
    rep = map_representation()
    rep.elements.append(rep.elements[1].model_copy(update={"id": "overlapping-city"}))
    document = prepare_document(rep)
    assert document.report["status"] == "blocked"
    assert any(issue["code"] == "label_clearance" for issue in document.report["issues"])
    assert ET.fromstring(render_svg(rep)).tag.endswith("svg")
    before = render_svg(rep)
    for name, exporter in exporters.items():
        data = exporter.render(rep)
        if name == 'svg':
            assert data.decode() == before
        elif name == 'pdf':
            assert len(PdfReader(BytesIO(data)).pages) == 1
        elif name == 'png':
            with Image.open(BytesIO(data)) as image:
                image.verify()
        else:
            with ZipFile(BytesIO(data)) as archive:
                assert 'tactile-map.pdf' in archive.namelist()
    assert render_svg(rep) == before


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
