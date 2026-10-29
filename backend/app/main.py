"""
FastAPI application entrypoint.

App bootstrap, CORS, root + health endpoints, database initialization,
route registration, and a top-level error handler that keeps internal
exception details out of API responses. New routers (recommendations,
reports) are included here as later phases add them.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import identities, projects, upload
from app.config import settings
from app.database.database import init_db

logger = logging.getLogger("software_metrics_advisor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: make sure the SQLite tables exist.
    init_db()
    yield
    # Shutdown: nothing to clean up yet.


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Last-resort handler for anything that isn't already an HTTPException
    (those — 400s, 404s, 413s raised deliberately in the routers — keep
    their own status code and message). Logs the real exception
    server-side for debugging, but never returns a Python traceback or
    exception message to the client.
    """
    logger.exception("Unhandled exception while processing %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred while processing the request."},
    )


app.include_router(upload.router)
app.include_router(projects.router)
app.include_router(identities.router)


@app.get("/")
def read_root():
    return {
        "message": f"{settings.APP_NAME} API",
        "status": "running",
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": settings.SERVICE_ID,
    }
