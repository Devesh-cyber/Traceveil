
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, cases, evidence, health, matches, reports, scans, takedowns
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(cases.router, prefix="/api")
    app.include_router(evidence.router, prefix="/api")
    app.include_router(scans.router, prefix="/api")
    app.include_router(matches.router, prefix="/api")
    app.include_router(reports.router, prefix="/api")
    app.include_router(takedowns.router, prefix="/api")

    return app


app = create_app()