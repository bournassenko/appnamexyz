import math
import subprocess
import textwrap
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
import cairosvg
import manifold3d as manifold
import numpy as np
import trimesh
from PIL import Image
from shapely import affinity
from shapely.geometry.polygon import orient
from app.production.scene import ProductionScene, polygons


def export_stl(scene: ProductionScene, path: Path) -> dict:
    width = scene.document.report['page']['width_mm']
    height = scene.document.report['page']['height_mm']
    base = scene.options.base_thickness_mm
    solids = [manifold.Manifold.cube((width, height, base))]
    geometry = affinity.translate(affinity.scale(scene.graphics, yfact=-1, origin=(0, 0)), yoff=height)
    for polygon in polygons(geometry):
        polygon = orient(polygon, sign=1)
        contours = [np.asarray(polygon.exterior.coords)[:-1], *[np.asarray(ring.coords)[:-1] for ring in polygon.interiors]]
        solids.append(manifold.CrossSection(contours, manifold.FillRule.Positive).extrude(scene.options.height + 0.03).translate((0, 0, base - 0.03)))
    for x, y, radius in scene.braille_dots:
        solids.append(manifold.Manifold.sphere(1, 16).scale((radius, radius, scene.options.braille_height_mm)).trim_by_plane((0, 0, 1), -0.03).translate((x, height - y, base)))
    solid = manifold.Manifold.batch_boolean(solids, manifold.OpType.Add)
    if len(solid.decompose()) != 1:
        raise ValueError('The mesh contains disconnected parts. STL export was stopped.')
    mesh_data = solid.to_mesh()
    mesh = trimesh.Trimesh(vertices=mesh_data.vert_properties[:, :3], faces=mesh_data.tri_verts, process=False)
    if len(mesh.faces) > 2_000_000 or not mesh.is_watertight or not mesh.is_winding_consistent or mesh.volume <= 0:
        raise ValueError('The generated mesh failed solid geometry validation.')
    path.write_bytes(mesh.export(file_type='stl'))
    return {'units': 'mm', 'dimensions_mm': mesh.extents.tolist(), 'triangles': len(mesh.faces), 'watertight': True, 'connected_bodies': 1, 'hardware_validated': False}


def export_brf(scene: ProductionScene, path: Path) -> dict:
    root = ET.fromstring(scene.document.pages[scene.document.map_index].svg)
    title = (root.findtext('{http://www.w3.org/2000/svg}title') or 'Map').split(' — ')[0]
    rows = [str(title), '', 'Key', '']
    rows.extend(f"{entry['number']} {entry['text']}" for entry in scene.document.report['numeric_key'])
    lines: list[str] = []
    for row in rows:
        result = subprocess.run(['lou_translate', 'en-us-brf.dis,en-ueb-g2.ctb'], input=row + '\n', capture_output=True, text=True, timeout=10, check=True)
        cells = result.stdout.rstrip('\r\n')
        if any(not 32 <= ord(cell) <= 95 for cell in cells):
            raise ValueError('Liblouis returned unsupported BRF characters.')
        lines.extend(textwrap.wrap(cells, width=40, subsequent_indent='  ', break_long_words=False, break_on_hyphens=False) or [''])
    if any(len(line) > 40 for line in lines):
        raise ValueError('A braille word exceeds the 40-cell BRF page width.')
    pages = ['\r\n'.join(lines[index:index + 25]) for index in range(0, len(lines), 25)]
    path.write_bytes(('\f'.join(pages) + '\r\n').encode('ascii'))
    return {'scope': 'Title and complete numbered text key only; no map graphics.', 'cells_per_line': 40, 'lines_per_page': 25, 'pages': len(pages), 'table': 'en-ueb-g2.ctb', 'hardware_validated': False}


def export_png(scene: ProductionScene, path: Path) -> dict:
    page = scene.document.report['page']
    dpi = scene.options.raster_dpi
    width = math.ceil(page['width_mm'] / 25.4 * dpi)
    height = math.ceil(page['height_mm'] / 25.4 * dpi)
    if width * height > 24_000_000:
        raise ValueError('This sheet exceeds the 24 megapixel export limit. Choose 150 DPI or use the existing SVG/PDF export at actual size.')
    data = cairosvg.svg2png(bytestring=scene.document.pages[scene.document.map_index].svg.encode(), output_width=width, output_height=height)
    with Image.open(BytesIO(data)) as image:
        image.convert('RGB').save(path, dpi=(dpi, dpi))
    return {'width': width, 'height': height, 'dpi': dpi, 'hardware_validated': False}
