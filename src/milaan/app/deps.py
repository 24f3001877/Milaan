"""Shared FastAPI dependencies: DB session, bearer-token auth, settings (TRD §2.4:
single static bearer token via API_TOKEN on all /api/* routes)."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Header, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from milaan.app.settings import Settings, get_settings

_engine = None
_SessionLocal = None


def _get_engine(settings: Settings):
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(settings.database_url_sync)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_db(settings: Settings = Depends(get_settings)) -> Generator[Session, None, None]:
    _get_engine(settings)
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


def require_bearer_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.api_token:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


def require_development_env(settings: Settings = Depends(get_settings)) -> None:
    """Gates dev-only routes (like /dev/seed) at route registration time per Appflow §4.4:
    '/dev/seed cannot exist outside development, enforced at route registration rather
    than by a runtime check.' This dependency is that enforcement point."""
    if not settings.dev_seed_actually_enabled:
        raise HTTPException(status_code=404, detail="Not found")
