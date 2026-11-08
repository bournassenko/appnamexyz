import asyncio
import json
import logging
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from app.analyzers.engines import analyze
from app.config import settings
from app.exporters.render import exporters, render_svg, validate_layout
from app.exporters.tactile import TactileValidationError, prepare_document
from app.refinement import apply_plan, plan_edit
from app.schemas import ElementUpdate, OutputSettings, ProjectConfig, RefineRequest, Representation, Source
from app.storage.local import storage
from app.production.api import ProductionManager

logger = logging.getLogger("application")
Image.MAX_IMAGE_PIXELS = 24_000_000
jobs: dict[str, dict] = {}
active_projects: set[str] = set()
semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
tasks: set[asyncio.Task] = set()
state_path = settings.storage_path / "processing-state.json"


def enabled() -> bool:
    return json.loads(state_path.read_text()).get("enabled", True) if state_path.exists() else True


def require_processing() -> None:
    if not enabled():
        raise HTTPException(503, "New processing is currently disabled. Existing previews and exports remain available.")


def project(project_id: str) -> dict:
    try:
        return storage.read(project_id, "project.json")
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "This project does not exist or its temporary session has expired.")


def representation(project_id: str) -> Representation:
    project(project_id)
    try:
        return Representation.model_validate(storage.read(project_id, "representation.json"))
    except FileNotFoundError:
        raise HTTPException(409, "Analyze the document before opening its representation.")


def save_revision(project_id: str, rep: Representation) -> None:
    meta = project(project_id)
    index = meta.get("revision", -1) + 1
    storage.write(project_id, f"revision-{index}.json", rep.model_dump())
    storage.write(project_id, "representation.json", rep.model_dump())
    meta.update(revision=index, latest_revision=index)
    storage.write(project_id, "project.json", meta)


async def cleanup_loop() -> None:
    while True:
        storage.cleanup(active_projects | production.active_projects)
        for key in list(jobs):
            if time.time() - jobs[key]["created_at"] > settings.session_hours * 3600 and jobs[key]["status"] in ("completed", "failed"):
                del jobs[key]
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for path in settings.storage_path.glob("*/job.json"):
        data = json.loads(path.read_text())
        if data["status"] in ("queued", "running"):
            data.update(status="failed", error="The server restarted during processing. Please start analysis again.")
            path.write_text(json.dumps(data))
    cleaner = asyncio.create_task(cleanup_loop())
    yield
    cleaner.cancel()
    for task in tasks:
        task.cancel()


app = FastAPI(title="Accessible materials API", version="1.0", lifespan=lifespan)
production = ProductionManager(lambda: storage, representation, semaphore, tasks)
app.include_router(production.router)
app.add_middleware(CORSMiddleware, allow_origins=[o for o in settings.allowed_origins.split(",") if o], allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type"])


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": {"message": str(exc.detail), "status": exc.status_code}}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse({"error": {"message": "Invalid request. Check file, identifier, and configuration values.", "status": 422}}, status_code=422)


@app.exception_handler(TactileValidationError)
async def tactile_error(request: Request, exc: TactileValidationError):
    return JSONResponse({"error": {"message": str(exc), "status": 422}, "validation": exc.report}, status_code=422)


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "version": "1.0", "release": settings.release_tag}


@app.get("/api/v1/branding")
def branding():
    return {"name": settings.app_name, "tagline": settings.app_tagline, "description": settings.app_description, "themeColor": settings.theme_color, "publicUrl": settings.public_url, "logo": settings.app_logo, "favicon": settings.app_favicon, "defaultAccessibility": {"theme": "system", "textSize": 100, "reducedMotion": False}, "features": {"persistentStorage": False, "formats": list(exporters)}}


@app.get("/api/v1/status")
def status():
    return {"enabled": enabled(), "ai_available": bool(settings.openai_api_key), "active_jobs": len(active_projects), "session_hours": settings.session_hours, "max_upload_mb": settings.max_upload_mb}


def set_processing(value: bool) -> dict:
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"enabled": value}))
    temporary.replace(state_path)
    return status()


@app.post("/api/v1/enable")
def enable():
    return set_processing(True)


@app.post("/api/v1/disable")
def disable():
    return set_processing(False)


@app.get("/api/v1/engines")
def list_engines():
    return [{"id": name, "name": title, "available": available, "description": description} for name, title, available, description in [("auto", "Automatic", True, "Hybrid with AI when configured; otherwise OpenCV."), ("openai", "OpenAI Vision", bool(settings.openai_api_key), "Semantic interpretation with approximate geometry."), ("opencv", "Computer vision", True, "Deterministic outlines without semantic classification."), ("ocr", "Text recognition", True, "Tesseract text and label locations."), ("hybrid", "Hybrid", True, "Combine outlines, OCR, and optional AI interpretation.")]]


@app.get("/api/v1/profiles")
def profiles():
    return [{"id": "bana2022", "name": "BANA 2022 preparation", "validated_hardware": False}, {"id": "digital", "name": "Digital preview", "validated_hardware": False}, {"id": "print", "name": "Printable document", "validated_hardware": False}, {"id": "swell", "name": "Swell paper oriented", "validated_hardware": False}]


@app.get("/api/v1/formats")
def formats():
    return [{"id": e.id, "name": e.name, "mime_type": e.mime_type, "extension": e.extension, "capabilities": e.capabilities, "hardware": e.hardware, "supported_document_types": ["map"]} for e in exporters.values()]


@app.post("/api/v1/projects", status_code=201)
async def upload(file: UploadFile = File(...)):
    storage.cleanup(active_projects | production.active_projects)
    if len([p for p in settings.storage_path.iterdir() if p.is_dir()]) >= settings.max_projects:
        raise HTTPException(503, "Temporary storage is full. Try again after existing sessions expire.")
    if file.size is not None and file.size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"Upload an image smaller than {settings.max_upload_mb} MB.")
    async with semaphore:
        return await asyncio.to_thread(store_upload, file)


def store_upload(file: UploadFile) -> dict:
    source_file = file.file
    source_file.seek(0, 2)
    size = source_file.tell()
    source_file.seek(0)
    if size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"Upload an image no larger than {settings.max_upload_mb} MB.")
    if shutil.disk_usage(settings.storage_path).free < size + 128 * 1024 * 1024:
        raise HTTPException(507, "Temporary storage has insufficient free space for this upload.")
    try:
        original = Image.open(source_file)
        if original.format not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("Unsupported image format")
        if original.width * original.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError("Image exceeds the pixel limit")
        original.verify()
        source_file.seek(0)
        image = ImageOps.exif_transpose(Image.open(source_file)).convert("RGB")
        if image.width < 32 or image.height < 32:
            raise ValueError("Image is too small")
        image.thumbnail((1800, 1800))
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise HTTPException(422, "Upload a valid PNG, JPEG, or WebP image between 32 pixels and 24 megapixels.")
    project_id = str(uuid4())
    path = storage.project_path(project_id)
    path.mkdir()
    try:
        source_file.seek(0)
        with (path / "original").open("wb") as destination:
            shutil.copyfileobj(source_file, destination, length=1024 * 1024)
        image.save(path / "input.png")
    except OSError as error:
        shutil.rmtree(path)
        raise HTTPException(507, "The upload could not be stored. Try again after temporary sessions expire.") from error
    source = Source(filename=Path(file.filename or "map").name[:160], width=image.width, height=image.height, mime_type=Image.MIME.get(original.format, "application/octet-stream"))
    meta = {"id": project_id, "created_at": time.time(), "source": source.model_dump(), "config": ProjectConfig().model_dump(), "revision": -1, "latest_revision": -1}
    storage.write(project_id, "project.json", meta)
    return meta


@app.get("/api/v1/projects/{project_id}")
def get_project(project_id: str):
    return project(project_id)


@app.get("/api/v1/projects/{project_id}/source")
def original_source(project_id: str):
    meta = project(project_id)
    return FileResponse(storage.project_path(project_id) / "original", media_type=meta["source"]["mime_type"], headers={"X-Content-Type-Options": "nosniff"})


@app.patch("/api/v1/projects/{project_id}/configuration")
def update_configuration(project_id: str, config: ProjectConfig):
    meta = project(project_id)
    if project_id in active_projects:
        raise HTTPException(409, "Wait for the current job before changing configuration.")
    meta["config"] = config.model_dump()
    storage.write(project_id, "project.json", meta)
    return meta


async def run_job(job_id: str, project_id: str, kind: str, instruction: str = "") -> None:
    job = jobs[job_id]
    try:
        async with semaphore:
            require_processing()
            job.update(status="running", progress=20, stage="Interpreting source" if kind == "analysis" else "Planning structured edits")
            storage.write(project_id, "job.json", job)
            meta = project(project_id)
            if kind == "analysis":
                with Image.open(storage.project_path(project_id) / "input.png") as image:
                    rep = await asyncio.wait_for(analyze(project_id, image, Source.model_validate(meta["source"]), ProjectConfig.model_validate(meta["config"])), timeout=180)
                message = "Map interpretation is ready. Review uncertain elements before export."
            else:
                rep = representation(project_id)
                plan = await asyncio.wait_for(plan_edit(instruction, rep), timeout=90)
                message = plan.message
                if plan.clarification:
                    job.update(status="completed", progress=100, stage="Clarification needed", message=message, clarification=True)
                    return
                rep = apply_plan(rep, plan)
            job.update(progress=85, stage="Validating representation and rendering")
            rep.warnings = list(dict.fromkeys(rep.warnings + await asyncio.to_thread(validate_layout, rep)))
            await asyncio.to_thread(render_svg, rep)
            save_revision(project_id, rep)
            job.update(status="completed", progress=100, stage="Ready for review", message=message)
    except Exception as exc:
        logger.warning("Processing failed: %s", type(exc).__name__)
        message = str(exc) if isinstance(exc, (ValueError, HTTPException)) else "Processing failed. Check the selected engine, server configuration, or image quality and try again."
        if isinstance(exc, HTTPException):
            message = str(exc.detail)
        job.update(status="failed", stage="Processing failed", error=message)
    finally:
        active_projects.discard(project_id)
        storage.write(project_id, "job.json", job)


def start_job(project_id: str, kind: str, instruction: str = "") -> dict:
    require_processing()
    project(project_id)
    if project_id in active_projects:
        raise HTTPException(409, "This project is already processing.")
    if len(active_projects) >= settings.max_concurrent_jobs + 4:
        raise HTTPException(503, "The processing queue is full. Try again shortly.")
    job_id = str(uuid4())
    job = {"id": job_id, "project_id": project_id, "kind": kind, "status": "queued", "progress": 0, "stage": "Waiting for an available worker", "created_at": time.time()}
    jobs[job_id] = job
    active_projects.add(project_id)
    storage.write(project_id, "job.json", job)
    task = asyncio.create_task(run_job(job_id, project_id, kind, instruction))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return job


@app.post("/api/v1/projects/{project_id}/analyze", status_code=202)
async def start_analysis(project_id: str):
    return start_job(project_id, "analysis")


@app.post("/api/v1/projects/{project_id}/refine", status_code=202)
async def refine(project_id: str, request: RefineRequest):
    representation(project_id)
    return start_job(project_id, "refinement", request.instruction)


@app.get("/api/v1/projects/{project_id}/jobs/{job_id}")
def get_job(project_id: str, job_id: str):
    project(project_id)
    job = jobs.get(job_id)
    if job is None:
        try:
            job = storage.read(project_id, "job.json")
        except FileNotFoundError:
            raise HTTPException(404, "Job not found.")
    if job["id"] != job_id or job["project_id"] != project_id:
        raise HTTPException(404, "Job not found.")
    return job


@app.get("/api/v1/projects/{project_id}/representation")
def get_representation(project_id: str):
    return representation(project_id)


@app.get("/api/v1/projects/{project_id}/preview")
def preview(project_id: str, page: int | None = None, english: bool = False):
    try:
        svg = render_svg(representation(project_id), page, english)
    except ValueError as error:
        raise HTTPException(422, str(error))
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.get("/api/v1/projects/{project_id}/validation")
def get_validation(project_id: str):
    rep = representation(project_id)
    if rep.export_settings.profile != "bana2022":
        return {"status": "not_applicable", "automated_checks_passed": False, "certified": False, "issues": [], "manual_checks": [], "pages": [], "map_page_index": 0}
    return prepare_document(rep).report


@app.patch("/api/v1/projects/{project_id}/output")
def update_output(project_id: str, output: OutputSettings):
    if project_id in active_projects:
        raise HTTPException(409, "Wait for processing before changing the output layout.")
    rep = representation(project_id)
    rep.export_settings = output
    rep.modifications.append(f"Changed output profile to {output.profile}.")
    save_revision(project_id, rep)
    meta = project(project_id)
    meta["config"]["output"] = output.model_dump()
    storage.write(project_id, "project.json", meta)
    return rep


@app.patch("/api/v1/projects/{project_id}/elements/{element_id}")
def update_element(project_id: str, element_id: str, update: ElementUpdate):
    if project_id in active_projects:
        raise HTTPException(409, "Wait for processing to finish before editing.")
    rep = representation(project_id)
    element = next((e for e in rep.elements if e.id == element_id), None)
    if element is None:
        raise HTTPException(404, "Element not found.")
    for key, value in update.model_dump(exclude_none=True).items():
        setattr(element, key, value)
    rep.modifications.append(f"Updated element {element.id}.")
    save_revision(project_id, rep)
    return rep


@app.post("/api/v1/projects/{project_id}/history/{direction}")
def history(project_id: str, direction: str):
    if direction not in ("undo", "redo"):
        raise HTTPException(404, "Unknown history action.")
    if project_id in active_projects:
        raise HTTPException(409, "Wait for processing to finish before editing history.")
    meta = project(project_id)
    index = meta["revision"] + (-1 if direction == "undo" else 1)
    if not 0 <= index <= meta["latest_revision"]:
        raise HTTPException(409, "No further revisions are available.")
    rep = storage.read(project_id, f"revision-{index}.json")
    storage.write(project_id, "representation.json", rep)
    meta["revision"] = index
    meta["config"]["output"] = rep["export_settings"]
    storage.write(project_id, "project.json", meta)
    return rep


@app.post("/api/v1/projects/{project_id}/exports/{format_id}")
async def generate_export(project_id: str, format_id: str, page: int | None = None):
    rep = representation(project_id)
    exporter = exporters.get(format_id)
    if exporter is None:
        raise HTTPException(404, "Unsupported export format.")
    try:
        async with semaphore:
            data = await asyncio.to_thread(exporter.render, rep, page)
    except TactileValidationError:
        raise
    except ValueError as error:
        raise HTTPException(422, str(error))
    if representation(project_id).model_dump() != rep.model_dump():
        raise HTTPException(409, "The map changed during export. Generate the file again.")
    suffix = f"{page}." if page is not None and format_id in ("svg", "png") else ""
    path = storage.project_path(project_id) / f"export.{suffix}{exporter.extension}"
    path.write_bytes(data)
    warnings = await asyncio.to_thread(validate_layout, rep)
    filename = f"accessible-map-page-{page + 1}.{exporter.extension}" if suffix else f"accessible-map.{exporter.extension}"
    return {"url": f"/api/v1/projects/{project_id}/downloads/{format_id}" + (f"?page={page}" if suffix else ""), "size": len(data), "mime_type": exporter.mime_type, "filename": filename, "warnings": warnings, "single_page": format_id in ("svg", "png")}


@app.get("/api/v1/projects/{project_id}/downloads/{format_id}")
def download(project_id: str, format_id: str, page: int | None = None):
    project(project_id)
    exporter = exporters.get(format_id)
    if exporter is None:
        raise HTTPException(404, "Unsupported export format.")
    suffix = f"{page}." if page is not None and format_id in ("svg", "png") else ""
    path = storage.project_path(project_id) / f"export.{suffix}{exporter.extension}"
    if not path.exists():
        raise HTTPException(404, "Generate this export before downloading it.")
    filename = f"accessible-map-page-{page + 1}.{exporter.extension}" if suffix else f"accessible-map.{exporter.extension}"
    return FileResponse(path, media_type=exporter.mime_type, filename=filename)


@app.get("/api/v1/projects/{project_id}/diagnostics")
def diagnostics(project_id: str):
    meta = project(project_id)
    rep = representation(project_id)
    return {"processing": status(), "revision": meta["revision"], "latest_revision": meta["latest_revision"], "processing_metadata": rep.processing_metadata, "warnings": rep.warnings, "modifications": rep.modifications, "representation": rep, "ai_usage": "Token usage and costs are not estimated in this build.", "validation": get_validation(project_id)}
