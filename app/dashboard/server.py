from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.dashboard.routes import router
from app.dashboard.operations_routes import router as operations_router
from app.dashboard.operations_db import OperationsDB
import os

app = FastAPI(
    title="MinhDang Operations Dashboard API",
    description="Read-only operations API for MinhDang automation pipeline",
    version="1.0.0"
)

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Since it's bound to localhost by default
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

app.include_router(router)
app.include_router(operations_router)

from app.config.settings import Settings

settings = Settings.from_env()

# Mount the React build
dist_path = Path(__file__).parent.parent.parent / "dashboard" / "dist"
if dist_path.exists():
    app.mount("/assets", StaticFiles(directory=dist_path / "assets"), name="assets")
    
# Mount job data directory for evidence viewer
job_data_path = settings.job_data_dir
if job_data_path.exists():
    app.mount("/data/jobs", StaticFiles(directory=job_data_path), name="job_data")
    
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = dist_path / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(dist_path / "index.html")

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
