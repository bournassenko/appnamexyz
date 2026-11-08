import ctypes
from ctypes.util import find_library
from functools import lru_cache
from threading import RLock
from typing import TypedDict
from xml.etree import ElementTree as ET
from app.exporters.braille import BrailleError, CELL_PITCH_MM, DOT_DIAMETER_MM, DOT_PITCH_MM

translation_lock = RLock()
SVG_NS = "http://www.w3.org/2000/svg"


class CellRun(TypedDict):
    start: int
    count: int
    text: str


@lru_cache(maxsize=1)
def load_liblouis() -> tuple[ctypes.CDLL, type[ctypes.c_uint16] | type[ctypes.c_uint32]]:
    library_path = find_library("louis")
    if not library_path:
        raise BrailleError("Liblouis back-translation is unavailable.")
    try:
        library = ctypes.CDLL(library_path)
        library.lou_charSize.restype = ctypes.c_int
        size = library.lou_charSize()
        if size not in (2, 4):
            raise BrailleError("The installed Liblouis character encoding is unsupported.")
        character = ctypes.c_uint32 if size == 4 else ctypes.c_uint16
        library.lou_backTranslate.argtypes = [ctypes.c_char_p, ctypes.POINTER(character), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(character), ctypes.POINTER(ctypes.c_int), ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        library.lou_backTranslate.restype = ctypes.c_int
        return library, character
    except OSError as error:
        raise BrailleError("Liblouis back-translation could not be loaded.") from error


def back_translate(cells: str, table: str) -> tuple[str, list[CellRun]]:
    if table not in ("en-ueb-g1.ctb", "en-ueb-g2.ctb"):
        raise BrailleError("Unsupported braille table.")
    if len(cells) > 2000 or any(not 0x2800 <= ord(cell) <= 0x283F for cell in cells):
        raise BrailleError("Invalid six-dot braille input.")
    if not cells:
        return "", []
    with translation_lock:
        library, character = load_liblouis()
        capacity = len(cells) * 32 + 32
        source = (character * len(cells))(*map(ord, cells))
        destination = (character * capacity)()
        input_length = ctypes.c_int(len(cells))
        output_length = ctypes.c_int(capacity)
        output_positions = (ctypes.c_int * len(cells))()
        input_positions = (ctypes.c_int * capacity)()
        result = library.lou_backTranslate(table.encode(), source, ctypes.byref(input_length), destination, ctypes.byref(output_length), None, None, output_positions, input_positions, None, 0)
        if not result or input_length.value != len(cells) or not 0 <= output_length.value < capacity:
            raise BrailleError("Liblouis could not back-translate the complete braille label.")
        text = "".join(chr(destination[index]) for index in range(output_length.value))
        positions = list(output_positions)
    if not text:
        return "", []
    if any(position < 0 or position > len(text) for position in positions) or positions != sorted(positions):
        raise BrailleError("Liblouis returned an unsupported braille cell alignment.")
    runs: list[CellRun] = []
    start = 0
    while start < len(cells):
        end = start + 1
        while end < len(cells) and positions[end] == positions[start]:
            end += 1
        text_end = positions[end] if end < len(cells) else len(text)
        runs.append({"start": start, "count": end - start, "text": text[positions[start]:text_end]})
        start = end
    return text, runs


def render_english_preview(svg: str) -> str:
    root = ET.fromstring(svg)
    decoded: dict[tuple[str, str], tuple[str, list[CellRun]]] = {}
    for group in root.iter(f"{{{SVG_NS}}}g"):
        cells = group.get("data-braille")
        if cells is None:
            continue
        table = group.attrib["data-braille-table"]
        key = cells, table
        if key not in decoded:
            decoded[key] = back_translate(cells, table)
        text, runs = decoded[key]
        x, y = float(group.attrib["data-braille-x"]), float(group.attrib["data-braille-y"])
        group.clear()
        group.set("class", "braille-english")
        group.set("role", "img")
        group.set("aria-label", text or "Braille formatting indicator")
        ET.SubElement(group, f"{{{SVG_NS}}}title").text = f"{cells} → {text}"
        for run in runs:
            if not run["text"].strip():
                continue
            width = (run["count"] - 1) * CELL_PITCH_MM + DOT_PITCH_MM + DOT_DIAMETER_MM
            label = ET.SubElement(group, f"{{{SVG_NS}}}text", {"x": f"{x + run['start'] * CELL_PITCH_MM + width / 2:.5f}", "y": f"{y + 4.4:.5f}", "font-family": "monospace", "font-size": "3.6", "text-anchor": "middle", "fill": "black", "data-cell-start": str(run["start"]), "data-cell-count": str(run["count"])})
            if len(run["text"]) * 2.2 > width:
                label.set("textLength", f"{width:.5f}")
                label.set("lengthAdjust", "spacingAndGlyphs")
            label.text = run["text"]
    root.set("data-preview", "english")
    metadata = root.find(f"{{{SVG_NS}}}metadata")
    if metadata is not None:
        metadata.text = "English reading aid from displayed braille. Not a tactile production export."
    return ET.tostring(root, encoding="unicode")
