import copy
import time
from xml.etree import ElementTree as ET
import pytest
import trimesh
from PIL import Image
from app.production.scene import build_scene, path_runs
from app.production.models import ProductionOptions
from app.production.exports import export_stl, export_brf, export_png
from app.exporters.tactile import prepare_document
from test_tactile import map_representation
from test_workflow import client, image_bytes


def test_production_preserves_map_and_exact_braille_dots(tmp_path):
    rep = map_representation()
    before = copy.deepcopy(rep.model_dump())
    document = prepare_document(rep)
    svg = document.pages[document.map_index].svg
    dots = [[float(node.attrib[key]) for key in ('cx', 'cy', 'r')] for node in ET.fromstring(svg).iter() if node.get('class') == 'braille-dot']
    for mode in ('swell', 'print', 'emboss'):
        scene = build_scene(rep, ProductionOptions(mode=mode, raster_dpi=150))
        assert scene.braille_dots == dots
        assert scene.to_dict()['hardware_validated'] is False
        assert bool(scene.graphic_dots) == (mode == 'emboss')
        assert scene.document.pages[scene.document.map_index].svg == svg
    scene = build_scene(rep, ProductionOptions(mode='print', raster_dpi=150))
    stl = tmp_path / 'map.stl'
    result = export_stl(scene, stl)
    mesh = trimesh.load_mesh(stl, process=True)
    assert result['watertight'] and mesh.is_watertight and mesh.is_winding_consistent
    assert result['connected_bodies'] == 1 and mesh.volume > 0
    assert mesh.extents[0] == pytest.approx(scene.to_dict()['width_mm'], abs=0.001)
    assert mesh.extents[2] == pytest.approx(2.0, abs=0.001)
    png = tmp_path / 'map.png'
    result = export_png(scene, png)
    with Image.open(png) as image:
        assert image.size == (result['width'], result['height'])
        assert image.info['dpi'][0] == pytest.approx(150, abs=0.02)
    brf = tmp_path / 'map.brf'
    result = export_brf(scene, brf)
    data = brf.read_bytes()
    assert result['scope'].endswith('no map graphics.')
    assert b'#A' in data and b'#B' in data
    for page in data.decode('ascii').split('\f'):
        assert len(page.rstrip('\r\n').split('\r\n')) <= 25
        assert all(len(line) <= 40 for line in page.split('\r\n'))
        assert all(32 <= ord(char) <= 95 for line in page.split('\r\n') for char in line)
    assert rep.model_dump() == before
    assert prepare_document(rep).pages[document.map_index].svg == svg


def test_production_jobs_download_and_stale_revision(client, monkeypatch):
    import app.main as module
    upload = client.post('/api/v1/projects', files={'file': ('map.png', image_bytes(), 'image/png')}).json()
    identifier = upload['id']
    rep = map_representation()
    rep.project_id = identifier
    module.save_revision(identifier, rep)
    prefix = f'/api/v1/projects/{identifier}/production'
    assert client.post(prefix + '/preview', json={'mode': 'swell'}).json()['braille_dot_count'] > 0
    assert client.post(prefix + '/exports', json={'mode': 'swell', 'format': 'stl'}).status_code == 422
    result = client.post(prefix + '/exports', json={'mode': 'emboss', 'format': 'brf'})
    assert result.status_code == 202
    job_id = result.json()['id']
    for _ in range(200):
        job = client.get(prefix + f'/jobs/{job_id}').json()
        if job['status'] in ('completed', 'failed'):
            break
        time.sleep(0.03)
    assert job['status'] == 'completed', job
    assert client.get(prefix + f'/jobs/{job_id}/download').status_code == 200
    rep.description += ' Updated'
    module.save_revision(identifier, rep)
    assert client.get(prefix + f'/jobs/{job_id}/download').status_code == 409
    assert client.get(prefix + '/jobs/not-a-uuid').status_code == 404
    assert client.post(prefix + '/preview', json={'braille_height_mm': 9}).status_code == 422


def test_production_rejects_unsupported_path_and_profile():
    with pytest.raises(ValueError):
        path_runs('M0 0 C0 0 2 2 3 3')
    assert path_runs('M0 0L2 0L2 2Z')[0][1] is True
    rep = map_representation()
    rep.export_settings.profile = 'digital'
    with pytest.raises(ValueError):
        build_scene(rep, ProductionOptions())
