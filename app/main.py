from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.database import Database
from app.health import build_health_report
from app.settings import Settings


settings = Settings.load()
database = Database(settings.database_path)
database.initialize()

app = FastAPI(
    title="ReferenceSync",
    version=__version__,
    description="Local reference download and Eagle import engine",
)


@app.get("/")
def root() -> dict:
    return {
        "name": "ReferenceSync",
        "version": __version__,
        "status": "running",
    }


@app.get("/health")
def health() -> dict:
    return build_health_report(settings)


@app.get("/database/summary")
def database_summary() -> dict:
    return database.summary()
