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
from app.exporters.tactile import TactileValidationError, prepare_document
from app.schemas import Element, Geometry, Representation, Source
from app.storage.local import LocalTemporaryStorageProvider


def map_representation():
    return Representation(project_id="7b1e35aa-a340-44c4-b12a-96e718340c23", source=Source(filename="map.png", width=800, height=600, mime_type="image/png"), elements=[
        Element(id="border", category="borders", geometry=Geometry(type="polyline", coordinates=[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9], [0.1, 0.1]]), confidence=1, provenance="synthetic"),
        Element(id="city", category="cities", label="Oak Town", geometry=Geometry(type="point", coordinates=[[0.5, 0.5]]), confidence=1, provenance="synthetic"),
    ])


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


def test_long_labels_use_braille_keys_and_preserve_separate_key_pages():
    rep = map_representation()
    rep.elements[1].label = "Oak Town Northern Educational District"
    rep.export_settings.page_height_mm = 200
    document = prepare_document(rep)
    assert document.report["automated_checks_passed"], document.report["issues"]
    assert [page.role for page in document.pages] == ["key-1", "map"]
    assert document.map_index == 1
    label = document.report["labels"][0]
    assert 2 <= len(label["braille"]) <= 3
    assert any((ord(cell) - 0x2800) & 0b100100 for cell in label["braille"])
    assert label["key"].startswith("o")
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
