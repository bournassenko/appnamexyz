from typing import Literal
from pydantic import BaseModel, Field


class ProductionOptions(BaseModel):
    mode: Literal["swell", "print", "emboss"] = "swell"
    relief_height_mm: float | None = Field(default=None, ge=0.2, le=2)
    braille_height_mm: float = Field(default=0.48, ge=0.3, le=0.8)
    base_thickness_mm: float = Field(default=1.2, ge=0.6, le=5)
    raster_dpi: Literal[150, 300] = 300

    @property
    def height(self) -> float:
        return self.relief_height_mm or {"swell": 0.4, "print": 0.8, "emboss": 0.5}[self.mode]

    @property
    def base(self) -> float:
        return self.base_thickness_mm if self.mode == "print" else 0.15


class ProductionExportRequest(ProductionOptions):
    format: Literal["stl", "brf", "png"]
