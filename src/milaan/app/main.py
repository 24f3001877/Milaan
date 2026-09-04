"""FastAPI application entrypoint (TRD §2.5)."""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from milaan.app.api import assurance, exceptions, ingest, matches, runs
from milaan.app.deps import get_db
from milaan.app.settings import Settings, get_settings

logger = structlog.get_logger()

DEFAULT_CORS_ORIGINS = ["http://localhost:5173"]


def _boot_settings() -> Settings | None:
    """Settings needed at import time, for CORS and the SPA mount.

    Returns None rather than raising. These two values are wiring, not correctness: a missing
    DATABASE_URL should surface from the route that needs it, with the Pydantic error naming
    the variable, rather than as an import-time crash inside the ASGI loader. Importing this
    module without an environment also has to keep working — the integration tests do it.
    """
    try:
        return get_settings()
    except Exception as exc:  # noqa: BLE001
        logger.warning("boot_settings_unavailable", error=str(exc))
        return None


_settings = _boot_settings()

app = FastAPI(title="Milaan", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list if _settings else DEFAULT_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router)
app.include_router(runs.router)
app.include_router(exceptions.router)
app.include_router(assurance.router)
app.include_router(matches.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness — process is up. No dependency checks."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    """Readiness — DB reachable (TRD §2.5)."""
    try:
        db = next(get_db(settings=get_settings()))
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("readyz_failed", error=str(exc))
        return {"status": "not_ready"}


def _mount_spa(static_dir: str) -> None:
    """Serve the built SPA from this process when `frontend/dist` is present.

    Wired up last on purpose: the API routers, /docs, /healthz and /readyz are all registered
    before this, so they are matched first. In local development `frontend/dist` does not
    exist and the Vite dev server serves the SPA instead, so nothing here changes how the
    app runs on a laptop.
    """
    dist = Path(static_dir)
    index = dist / "index.html"
    if not index.is_file():
        logger.info("spa_not_served", static_dir=str(dist), reason="no index.html")
        return

    root = dist.resolve()
    assets = dist / "assets"
    if assets.is_dir():
        # Only /assets is mounted — content-hashed bundles and fonts, the bulk of the bytes,
        # served by StaticFiles for its caching and range handling. Nothing is mounted at
        # "/": a mount there matches every path, so an unknown route would be answered by a
        # static file server that only speaks GET, turning `POST /api/v1/typo` into 405
        # Method Not Allowed — a path that does not exist claiming the verb was the problem.
        app.mount("/assets", StaticFiles(directory=assets), name="spa-assets")

    @app.exception_handler(404)
    async def spa_fallback(request: Request, exc: Exception) -> Response:
        """Serve root-level static files, and the SPA shell for client-side routes.

        vue-router uses createWebHistory, so /runs/<id> is a client-side route: on a cold
        load the server sees a path it has never heard of, and returning the shell is what
        makes a deep link or a refresh work.

        Two exclusions keep the rest honest — /api/ so a bad API call still fails as JSON,
        and /assets/ so a missing hashed bundle fails as a missing file rather than
        silently returning a page of HTML with a 200.
        """
        path = request.url.path
        if request.method in ("GET", "HEAD") and not path.startswith(("/api/", "/assets/")):
            candidate = (dist / path.lstrip("/")).resolve()
            if root in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

        # Overriding the 404 handler covers deliberate 404s from API routes too, so the
        # original detail is passed through rather than flattened to "Not Found".
        detail = exc.detail if isinstance(exc, StarletteHTTPException) else "Not Found"
        return JSONResponse({"detail": detail}, status_code=404)

    logger.info("spa_served", static_dir=str(root))


_mount_spa(_settings.static_dir if _settings else "frontend/dist")
