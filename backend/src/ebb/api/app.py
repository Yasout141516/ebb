"""The FastAPI app and its uvicorn factory."""

import psycopg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ebb.api.errors import install_error_handlers
from ebb.api.routes_notes import router as notes_router
from ebb.api.routes_study import router as study_router
from ebb.api.routes_subjects import router as subjects_router
from ebb.api.schemas import Health
from ebb.api.services import Services, build_services
from ebb.config import Settings
from ebb.db.pool import DATABASE_DOWN


def create_app(services: Services) -> FastAPI:
    app = FastAPI(title="Ebb")
    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[services.settings.ebb_web_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(subjects_router)
    app.include_router(notes_router)
    app.include_router(study_router)

    @app.get("/health")
    def health() -> Health:
        try:
            services.store.ping()
            database = "ok"
        except psycopg.Error:
            database = DATABASE_DOWN
        return Health(database=database, tutor=services.config_error or "ok")

    return app


def app_from_env() -> FastAPI:
    """The factory `uvicorn --factory` calls."""
    return create_app(build_services(Settings()))
