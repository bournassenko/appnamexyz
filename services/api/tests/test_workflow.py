import asyncio
import importlib
import json
import os
import time
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from pypdf import PdfReader
from app.schemas import Element, Geometry, Representation, Source
from app.refinement import apply_plan, deterministic_plan
from app.storage.local import LocalTemporaryStorageProvider


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as module
    monkeypatch.setattr(module, "storage", LocalTemporaryStorageProvider(tmp_path))
    monkeypatch.setattr(module, "state_path", tmp_path / "processing-state.json")
    monkeypatch.setattr(module.settings, "storage_path", tmp_path)
    monkeypatch.setattr(module.settings, "openai_api_key", "")
    module.jobs.clear()
    module.active_projects.clear()
    with TestClient(module.app) as value:
        yield value


def image_bytes():
    image = Image.new("RGB", (500, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.polygon([(50, 40), (430, 60), (400, 330), (90, 360)], outline="black", width=5)
    draw.line([(100, 80), (170, 160), (130, 240), (220, 320)], fill="black", width=4)
    draw.text((260, 170), "North", fill="black")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def wait(client, project_id, job_id):
    for _ in range(100):
        job = client.get(f"/api/v1/projects/{project_id}/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            assert job["status"] == "completed", job
            return job
        time.sleep(0.05)
    pytest.fail("Job did not complete")


def test_complete_map_workflow_and_actual_exports(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"
    upload = client.post("/api/v1/projects", files={"file": ("classroom.png", image_bytes(), "image/png")})
    assert upload.status_code == 201
    project = upload.json()
    project_id = project["id"]
    config = project["config"]
    config["engine"] = "opencv"
    config["output"]["profile"] = "digital"
    assert client.patch(f"/api/v1/projects/{project_id}/configuration", json=config).status_code == 200
    job = client.post(f"/api/v1/projects/{project_id}/analyze").json()
    wait(client, project_id, job["id"])
    rep = client.get(f"/api/v1/projects/{project_id}/representation").json()
    assert len(rep["elements"]) > 0
    element = rep["elements"][0]
    assert client.patch(f"/api/v1/projects/{project_id}/elements/{element['id']}", json={"category": "rivers", "label": "Test river"}).status_code == 200
    job = client.post(f"/api/v1/projects/{project_id}/refine", json={"instruction": "Remove the rivers"}).json()
    wait(client, project_id, job["id"])
    rep = client.get(f"/api/v1/projects/{project_id}/representation").json()
    assert next(e for e in rep["elements"] if e["id"] == element["id"])["visible"] is False
    assert client.post(f"/api/v1/projects/{project_id}/history/undo").status_code == 200
    assert client.post(f"/api/v1/projects/{project_id}/history/redo").status_code == 200
    for format_id in ("svg", "pdf", "png"):
        result = client.post(f"/api/v1/projects/{project_id}/exports/{format_id}")
        assert result.status_code == 200, result.text
        content = client.get(result.json()["url"]).content
        assert len(content) > 100
        if format_id == "svg":
            assert ElementTree.fromstring(content).tag.endswith("svg")
        elif format_id == "pdf":
            assert len(PdfReader(BytesIO(content)).pages) == 1
        else:
            with Image.open(BytesIO(content)) as image:
                assert image.format == "PNG"
                assert image.width == 1800
    assert client.post("/api/v1/disable").json()["enabled"] is False
    assert client.post(f"/api/v1/projects/{project_id}/analyze").status_code == 503
    assert client.post(f"/api/v1/projects/{project_id}/refine", json={"instruction": "Show rivers"}).status_code == 503
    assert client.get(f"/api/v1/projects/{project_id}/preview").status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/downloads/svg").status_code == 200
    assert client.post("/api/v1/enable").json()["enabled"] is True


def test_upload_validation_and_project_isolation(client):
    assert client.post("/api/v1/projects", files={"file": ("bad.png", b"not an image", "image/png")}).status_code == 422
    assert client.get("/api/v1/projects/not-a-project").status_code == 404
    assert client.get("/api/v1/engines").json()[1]["available"] is False


def test_upload_above_previous_limit_preserves_original(client):
    content = image_bytes() + bytes(13 * 1024 * 1024)
    response = client.post("/api/v1/projects", files={"file": ("large.png", content, "image/png")})
    assert response.status_code == 201, response.text
    assert client.get("/api/v1/status").json()["max_upload_mb"] == 1024
    assert client.get(f"/api/v1/projects/{response.json()['id']}/source").content == content


def test_one_gib_upload_boundary_without_allocating_in_memory(client, tmp_path):
    import app.main as module
    from starlette.datastructures import UploadFile
    from fastapi import HTTPException
    with (tmp_path / "oversized-upload").open("wb+") as file:
        file.truncate(1024 ** 3 + 1)
        with pytest.raises(HTTPException) as error:
            module.store_upload(UploadFile(file=file, filename="oversized.png"))
        assert error.value.status_code == 413


def test_bana_output_updates_validation_and_export_gate_without_ai(client):
    import app.main as module
    from test_tactile import map_representation
    uploaded = client.post("/api/v1/projects", files={"file": ("map.png", image_bytes(), "image/png")}).json()
    project_id = uploaded["id"]
    rep = map_representation()
    rep.project_id = project_id
    module.save_revision(project_id, rep)
    assert client.get(f"/api/v1/projects/{project_id}/validation").json()["automated_checks_passed"]
    english = client.get(f"/api/v1/projects/{project_id}/preview?english=true")
    assert english.status_code == 200
    assert ElementTree.fromstring(english.content).attrib["data-preview"] == "english"
    result = client.post(f"/api/v1/projects/{project_id}/exports/bundle")
    assert result.status_code == 200, result.text
    assert client.get(result.json()["url"]).status_code == 200
    output = rep.export_settings.model_dump()
    output["legend"] = False
    assert client.patch(f"/api/v1/projects/{project_id}/output", json=output).status_code == 200
    assert client.get(result.json()["url"]).status_code == 404
    blocked = client.post(f"/api/v1/projects/{project_id}/exports/pdf")
    assert blocked.status_code == 422
    assert blocked.json()["validation"]["status"] == "blocked"
    assert client.get(f"/api/v1/projects/{project_id}/preview").status_code == 200
    assert client.post(f"/api/v1/projects/{project_id}/history/undo").status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}/validation").json()["automated_checks_passed"]


def test_switch_is_written_to_persistent_state(client, tmp_path):
    client.post("/api/v1/disable")
    assert json.loads((tmp_path / "processing-state.json").read_text()) == {"enabled": False}
    assert client.get("/api/v1/health").status_code == 200


def test_temporary_cleanup_preserves_active_projects(tmp_path):
    storage = LocalTemporaryStorageProvider(tmp_path)
    old = tmp_path / "old"
    active = tmp_path / "active"
    old.mkdir()
    active.mkdir()
    os.utime(old, (0, 0))
    os.utime(active, (0, 0))
    assert storage.cleanup({"active"}) == 1
    assert active.exists()


def test_structured_edits_and_escaping():
    from app.exporters.render import render_svg
    rep = Representation(project_id="test", source=Source(filename="test", width=500, height=400, mime_type="image/png"), elements=[Element(id="river", category="rivers", geometry=Geometry(type="polyline", coordinates=[[0.1, 0.1], [0.5, 0.5]]), label='<script>alert("x")</script>', confidence=0.5, provenance="test")])
    plan = deterministic_plan("Remove the rivers")
    changed = apply_plan(rep, plan)
    assert not changed.elements[0].visible
    assert rep.elements[0].visible
    assert '<script>' not in render_svg(rep)
    assert deterministic_plan("Remove rivers but keep the main river") is None


def test_mocked_openai_analysis_validates_representation(client, monkeypatch):
    import app.analyzers.engines as module
    from app.schemas import Interpretation
    async def mock_analysis(self, image, config):
        return Interpretation(map_type="educational", description="A synthetic river.", warnings=["Synthetic test"], elements=[Element(id="river", category="rivers", geometry=Geometry(type="polyline", coordinates=[[0.2, 0.1], [0.4, 0.8]]), confidence=0.7, provenance="mock")])
    monkeypatch.setattr(module.OpenAIEngine, "analyze", mock_analysis)
    project = client.post("/api/v1/projects", files={"file": ("test.png", image_bytes(), "image/png")}).json()
    config = project["config"]
    config["engine"] = "openai"
    client.patch(f"/api/v1/projects/{project['id']}/configuration", json=config)
    job = client.post(f"/api/v1/projects/{project['id']}/analyze").json()
    wait(client, project["id"], job["id"])
    rep = client.get(f"/api/v1/projects/{project['id']}/representation").json()
    assert rep["elements"][0]["category"] == "rivers"


def test_hybrid_reads_independent_images(client):
    project = client.post("/api/v1/projects", files={"file": ("map.png", image_bytes(), "image/png")}).json()
    config = project["config"]
    config["engine"] = "hybrid"
    client.patch(f"/api/v1/projects/{project['id']}/configuration", json=config)
    for _ in range(3):
        job = client.post(f"/api/v1/projects/{project['id']}/analyze").json()
        wait(client, project["id"], job["id"])
    result = client.get(f"/api/v1/projects/{project['id']}/representation").json()
    assert result["processing_metadata"]["engine"] == "hybrid"
    assert result["elements"]
    assert any("OpenAI is not configured" in warning for warning in result["warnings"])


def test_exported_labels_remain_visible_in_png_and_pdf():
    from app.exporters.render import exporters
    from app.schemas import OutputSettings
    rep = Representation(project_id="label-test", source=Source(filename="labels.png", width=500, height=400, mime_type="image/png"), elements=[Element(id="label", category="labels", geometry=Geometry(type="point", coordinates=[[0.2, 0.25]]), label="Readable Town", confidence=1, provenance="test")], export_settings=OutputSettings(profile="digital", legend=False, label_size=22))
    png = exporters["png"].render(rep)
    with Image.open(BytesIO(png)).convert("L") as image:
        label_region = image.crop((400, 345, 800, 455))
        assert sum(pixel < 100 for pixel in label_region.tobytes()) > 300
    pdf = PdfReader(BytesIO(exporters["pdf"].render(rep)))
    assert "Readable Town" in pdf.pages[0].extract_text()


def test_removing_a_named_feature_also_removes_its_duplicate_label():
    from app.analyzers.engines import consolidate_labels
    feature = Element(id="river", category="rivers", geometry=Geometry(type="polyline", coordinates=[[0.3, 0.1], [0.4, 0.7]]), label="Azure River", confidence=0.9, provenance="test")
    duplicate = Element(id="river-label", category="labels", geometry=Geometry(type="point", coordinates=[[0.4, 0.4]]), label="Azure River", confidence=0.9, provenance="test")
    independent = Element(id="note", category="labels", geometry=Geometry(type="point", coordinates=[[0.7, 0.4]]), label="North", confidence=0.9, provenance="test")
    rep = Representation(project_id="test", source=Source(filename="map", width=600, height=400, mime_type="image/png"), elements=consolidate_labels([feature, duplicate, independent]))
    assert [element.id for element in rep.elements] == ["river", "note"]
    result = apply_plan(rep, deterministic_plan("Remove the rivers"))
    from app.exporters.render import render_svg
    svg = render_svg(result)
    assert "Azure River" not in svg
    assert "North" in svg
