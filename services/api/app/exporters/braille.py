import subprocess
from dataclasses import dataclass, field
from html import escape

DOT_DIAMETER_MM = 0.057 * 25.4
DOT_PITCH_MM = 0.092 * 25.4
CELL_PITCH_MM = 0.245 * 25.4
LINE_PITCH_MM = 0.400 * 25.4
CELL_HEIGHT_MM = 2 * DOT_PITCH_MM + DOT_DIAMETER_MM
OPEN_NOTE = "⠈⠨⠣"
CLOSE_NOTE = "⠈⠨⠜"


class BrailleError(ValueError):
    pass


@dataclass
class BrailleTranslator:
    table: str
    cache: dict[tuple[str, str], str] = field(default_factory=dict)

    def translate(self, text: str, table: str | None = None) -> str:
        selected = table or self.table
        if selected not in ("en-ueb-g1.ctb", "en-ueb-g2.ctb"):
            raise BrailleError("Unsupported braille table.")
        normalized = " ".join(text.split())
        key = selected, normalized
        if key in self.cache:
            return self.cache[key]
        if not normalized:
            return ""
        try:
            result = subprocess.run(["lou_translate", f"unicode.dis,{selected}"], input=normalized + "\n", text=True, capture_output=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            raise BrailleError("Liblouis braille translation is unavailable. Production export is blocked.") from error
        cells = result.stdout.rstrip("\r\n").replace(" ", "⠀")
        if any(not 0x2800 <= ord(character) <= 0x283F for character in cells):
            raise BrailleError("The label contains characters that cannot be represented by the selected six-dot UEB table.")
        self.cache[key] = cells
        return cells


def braille_width(cells: str) -> float:
    return max(0, len(cells) - 1) * CELL_PITCH_MM + DOT_PITCH_MM + DOT_DIAMETER_MM if cells else 0


def wrap_braille(cells: str, columns: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in cells.split("⠀"):
        if len(word) > columns:
            raise BrailleError("A braille word exceeds the available page width. Shorten the title or label, or use a larger page.")
        candidate = current + ("⠀" if current else "") + word
        if len(candidate) > columns:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def draw_braille(cells: str, x: float, y: float, accessible_text: str) -> str:
    parts = [f'<g role="img" aria-label="{escape(accessible_text, quote=True)}"><title>{escape(accessible_text)}</title>']
    radius = DOT_DIAMETER_MM / 2
    for index, character in enumerate(cells):
        pattern = ord(character) - 0x2800
        for dot in range(6):
            if pattern & (1 << dot):
                cx = x + index * CELL_PITCH_MM + (dot // 3) * DOT_PITCH_MM + radius
                cy = y + (dot % 3) * DOT_PITCH_MM + radius
                parts.append(f'<circle class="braille-dot" cx="{cx:.5f}" cy="{cy:.5f}" r="{radius:.5f}" fill="black"/>')
    parts.append('</g>')
    return "".join(parts)
