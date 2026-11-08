import json
import shutil
import time
from pathlib import Path
from typing import Protocol
from uuid import UUID
from app.config import settings


class StorageProvider(Protocol):
    def project_path(self, project_id: str) -> Path: ...
    def read(self, project_id: str, name: str) -> dict: ...
    def write(self, project_id: str, name: str, value: dict) -> None: ...


class LocalTemporaryStorageProvider:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def project_path(self, project_id: str) -> Path:
        UUID(project_id)
        return self.root / project_id

    def read(self, project_id: str, name: str) -> dict:
        return json.loads((self.project_path(project_id) / name).read_text())

    def write(self, project_id: str, name: str, value: dict) -> None:
        path = self.project_path(project_id) / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value))
        temporary.replace(path)
        if name == "representation.json":
            for generated in path.parent.glob("export.*"):
                generated.unlink(missing_ok=True)

    def cleanup(self, active: set[str] | None = None) -> int:
        removed = 0
        for path in self.root.iterdir():
            if path.is_dir() and path.name not in (active or set()) and time.time() - path.stat().st_mtime > settings.session_hours * 3600:
                shutil.rmtree(path)
                removed += 1
        return removed


storage = LocalTemporaryStorageProvider(settings.storage_path)
