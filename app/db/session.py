from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import Base

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """
    Schema is now owned by Alembic migrations (see /migrations), not this
    function. This is kept ONLY as a safety net for local dev/tests that
    haven't run `alembic upgrade head` yet: it creates any tables missing
    from the current DB (via checkfirst, the create_all default) but never
    alters existing tables, so it cannot substitute for running real
    migrations when the schema changes. Production deployments should run
    `alembic upgrade head` as a release step and can skip calling this.
    """
    Base.metadata.create_all(bind=engine, checkfirst=True)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
