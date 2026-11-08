from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

Category = Literal["borders", "rivers", "roads", "regions", "cities", "labels", "landmarks", "other"]


class Geometry(BaseModel):
    type: Literal["point", "polyline", "polygon", "multipart"]
    coordinates: list[list[float]] = Field(min_length=1, max_length=2000)
    parts: list[list[list[float]]] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_coordinates(self):
        for part in [self.coordinates, *self.parts]:
            if len(part) > 2000:
                raise ValueError("Geometry contains too many vertices")
            for point in part:
                if len(point) != 2 or any(not 0 <= value <= 1 for value in point):
                    raise ValueError("Coordinates must be normalized between zero and one")
        return self


class Style(BaseModel):
    line_width: float = Field(default=1.2, ge=0.3, le=5)
    label_size: float = Field(default=14, ge=8, le=40)
    symbol_size: float = Field(default=5, ge=2, le=20)
    texture: Literal["none", "dots", "diagonal"] = "none"


class TactileProperties(BaseModel):
    raised: bool = True
    notes: str = ""


class Element(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    category: Category
    geometry: Geometry
    label: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=600)
    confidence: float = Field(ge=0, le=1)
    provenance: str = Field(max_length=100)
    visible: bool = True
    priority: int = Field(default=5, ge=1, le=10)
    style: Style = Field(default_factory=Style)
    tactile_properties: TactileProperties = Field(default_factory=TactileProperties)


class OutputSettings(BaseModel):
    profile: Literal["digital", "print", "swell", "bana2022"] = "bana2022"
    page_width_mm: float = Field(default=292.1, ge=100, le=420)
    page_height_mm: float = Field(default=279.4, ge=100, le=420)
    line_width: float = Field(default=1.2, ge=0.3, le=5)
    label_size: float = Field(default=14, ge=8, le=40)
    symbol_size: float = Field(default=5, ge=2, le=20)
    minimum_spacing_mm: float = Field(default=3, ge=1, le=20)
    legend: bool = True
    texture: Literal["none", "dots", "diagonal"] = "none"
    contrast: Literal["standard", "high"] = "high"
    tactile_title: str = Field(default="Map", min_length=1, max_length=120)
    braille_table: Literal["en-ueb-g2.ctb"] = "en-ueb-g2.ctb"
    label_mode: Literal["numbered"] = "numbered"
    split_crowded_maps: Literal[False] = False
    max_map_views: int = Field(default=1, ge=1, le=1)
    key_position: Literal["below"] = "below"

    @field_validator("label_mode", mode="before")
    @classmethod
    def use_numbered_labels(cls, value: str) -> str:
        return "numbered" if value in ("auto", "full", "numbered") else value

    @field_validator("split_crowded_maps", mode="before")
    @classmethod
    def use_single_map(cls, value: bool) -> bool:
        return False

    @field_validator("key_position", mode="before")
    @classmethod
    def use_bottom_key(cls, value: str) -> str:
        return "below" if value in ("before", "below") else value

    @field_validator("braille_table", mode="before")
    @classmethod
    def migrate_braille_table(cls, value: str) -> str:
        return "en-ueb-g2.ctb" if value == "en-ueb-g1.ctb" else value

    @field_validator("max_map_views", mode="before")
    @classmethod
    def cap_map_views(cls, value: int) -> int:
        return min(value, 1) if isinstance(value, int) else value


class ProjectConfig(BaseModel):
    engine: Literal["auto", "openai", "opencv", "ocr", "hybrid"] = "auto"
    language: Literal["eng", "fra", "spa", "deu"] = "eng"
    map_type: str = Field(default="automatic", max_length=80)
    include: list[Category] = Field(default_factory=lambda: ["borders", "rivers", "roads", "regions", "cities", "labels", "landmarks", "other"])
    simplification: float = Field(default=0.003, ge=0, le=0.03)
    density: Literal["low", "balanced", "high"] = "balanced"
    preprocess: bool = True
    output: OutputSettings = Field(default_factory=OutputSettings)


class Source(BaseModel):
    filename: str
    width: int
    height: int
    mime_type: str


class Representation(BaseModel):
    schema_version: str = "1.0"
    project_id: str
    source: Source
    coordinate_system: str = "normalized_0_1"
    document_type: str = "map"
    map_type: str = "unknown"
    description: str = ""
    elements: list[Element] = Field(default_factory=list, max_length=300)
    relationships: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    processing_metadata: dict = Field(default_factory=dict)
    modifications: list[str] = Field(default_factory=list)
    export_settings: OutputSettings = Field(default_factory=OutputSettings)


class Interpretation(BaseModel):
    map_type: str
    description: str
    elements: list[Element] = Field(max_length=200)
    warnings: list[str]


class Operation(BaseModel):
    action: Literal["hide", "restore", "relabel", "simplify", "label_size", "line_width", "spacing", "texture", "legend", "density"]
    category: Category | None = None
    element_id: str | None = None
    text: str = Field(default="", max_length=160)
    value: float | None = None


class EditPlan(BaseModel):
    message: str
    clarification: bool = False
    operations: list[Operation] = Field(default_factory=list, max_length=30)


class RefineRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=1500)


class ElementUpdate(BaseModel):
    visible: bool | None = None
    label: str | None = Field(default=None, max_length=160)
    category: Category | None = None
