from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine = None
_session_local = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    return _engine


def _get_session_local():
    global _session_local
    if _session_local is None:
        _session_local = sessionmaker(autocommit=False, autoflush=False, bind=_get_engine())
    return _session_local


def __getattr__(name: str):
    # engine/SessionLocal are created lazily, on first actual access -- importing
    # this module (e.g. transitively via `get_db`) must not require a real Postgres
    # driver to be importable, since FastAPI routes and tests only ever need `get_db`.
    if name == "engine":
        return _get_engine()
    if name == "SessionLocal":
        return _get_session_local()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_db() -> Generator[Session, None, None]:
    db = _get_session_local()()
    try:
        yield db
    finally:
        db.close()
