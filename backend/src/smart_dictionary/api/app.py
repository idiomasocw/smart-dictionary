"""FastAPI application factory."""

from fastapi import FastAPI

from smart_dictionary import __version__
from smart_dictionary.api import errors
from smart_dictionary.api.routes import admin, entries, factory, health, search
from smart_dictionary.core.logging import configure_logging
from smart_dictionary.core.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    interactive_docs = settings.environment != "prod"
    app = FastAPI(
        title="Smart Dictionary API",
        version=__version__,
        docs_url="/docs" if interactive_docs else None,
        redoc_url=None,
    )
    errors.install(app)
    app.include_router(health.router)
    app.include_router(entries.router)
    app.include_router(search.router)
    app.include_router(factory.router)
    app.include_router(admin.router)
    return app
