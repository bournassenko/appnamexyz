import asyncio
import hashlib
import logging
from uuid import UUID, uuid4
from typing import Callable, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.production.models import ProductionOptions, ProductionExportRequest
from app.schemas import Representation
from app.storage.local import StorageProvider
from app.production.scene import build_scene
from app.production.exports import export_stl, export_brf, export_png

logger = logging.getLogger('application.production')


def revision_hash(rep: Representation) -> str:
    return hashlib.sha256(rep.model_dump_json().encode()).hexdigest()


class ProductionManager:
    def __init__(self, storage: Callable[[], StorageProvider], representation: Callable[[str], Representation], semaphore: asyncio.Semaphore, tasks: set[asyncio.Task]) -> None:
        self.storage = storage
        self.representation = representation
        self.semaphore = semaphore
        self.tasks = tasks
        self.active_projects: set[str] = set()
        self.running: set[str] = set()
        self.router = APIRouter(prefix='/api/v1/projects/{project_id}/production')
        self.router.add_api_route('/preview', self.preview, methods=['POST'])
        self.router.add_api_route('/exports', self.start_export, methods=['POST'], status_code=202)
        self.router.add_api_route('/jobs/{job_id}', self.job, methods=['GET'])
        self.router.add_api_route('/jobs/{job_id}/download', self.download, methods=['GET'])

    async def preview(self, project_id: str, options: ProductionOptions) -> dict:
        rep = self.representation(project_id)
        async with self.semaphore:
            try:
                return await asyncio.to_thread(lambda: build_scene(rep, options).to_dict())
            except ValueError as error:
                raise HTTPException(422, str(error))

    async def start_export(self, project_id: str, options: ProductionExportRequest) -> dict:
        rep = self.representation(project_id)
        if options.format == 'stl' and options.mode != 'print':
            raise HTTPException(422, 'Select 3D printing for STL export.')
        if project_id in self.active_projects:
            raise HTTPException(409, 'A production export is already running for this project.')
        identifier = str(uuid4())
        job = {'id': identifier, 'status': 'queued', 'stage': 'Waiting', 'progress': 0, 'source_hash': revision_hash(rep), 'format': options.format}
        self.storage().write(project_id, f'production-{identifier}.json', job)
        self.active_projects.add(project_id)
        self.running.add(identifier)
        task = asyncio.create_task(self.run(project_id, rep, options, job))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job

    async def run(self, project_id: str, rep: Representation, options: ProductionExportRequest, job: dict[str, Any]) -> None:
        try:
            async with self.semaphore:
                job.update(status='running', stage='Building production geometry', progress=15)
                self.storage().write(project_id, f"production-{job['id']}.json", job)
                scene = await asyncio.to_thread(build_scene, rep, options)
                job.update(stage='Generating and validating file', progress=40)
                self.storage().write(project_id, f"production-{job['id']}.json", job)
                filename = f"production-{job['id']}.{options.format}"
                path = self.storage().project_path(project_id) / filename
                metadata = await asyncio.to_thread({'stl': export_stl, 'brf': export_brf, 'png': export_png}[options.format], scene, path)
                job.update(status='completed', stage='Ready', progress=100, filename=filename, metadata=metadata, bytes=path.stat().st_size)
        except asyncio.CancelledError:
            job.update(status='failed', error='The server stopped during export. Please retry.')
            raise
        except Exception as error:
            logger.exception('Production export failed')
            job.update(status='failed', error=str(error) if isinstance(error, ValueError) else 'Production export failed. Please retry or simplify the map.')
        finally:
            self.storage().write(project_id, f"production-{job['id']}.json", job)
            self.active_projects.discard(project_id)
            self.running.discard(job['id'])

    def job(self, project_id: str, job_id: str) -> dict:
        rep = self.representation(project_id)
        try:
            UUID(job_id)
            job = self.storage().read(project_id, f'production-{job_id}.json')
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, 'Production export not found.')
        if job['source_hash'] != revision_hash(rep):
            raise HTTPException(409, 'The map has changed. Generate a new production export.')
        if job['status'] in ('queued', 'running') and job_id not in self.running:
            job.update(status='failed', error='The server restarted during export. Please retry.')
        return job

    def download(self, project_id: str, job_id: str) -> FileResponse:
        job = self.job(project_id, job_id)
        if job['status'] != 'completed':
            raise HTTPException(409, 'The production export is not ready.')
        path = self.storage().project_path(project_id) / job['filename']
        if not path.is_file():
            raise HTTPException(404, 'The generated file has expired. Generate it again.')
        return FileResponse(path, filename=f"map.{job['format']}", media_type={'stl': 'model/stl', 'brf': 'application/octet-stream', 'png': 'image/png'}[job['format']])
