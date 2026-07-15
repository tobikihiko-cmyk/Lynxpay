"""SQLAlchemy engine and request-scoped sessions."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings

engine_kwargs: dict = {"pool_pre_ping": True}
if settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
    if settings.DATABASE_URL in {"sqlite://", "sqlite:///:memory:"}:
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(settings.DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
worker_engine = (
    create_engine(settings.WORKER_DATABASE_URL, pool_pre_ping=True)
    if settings.WORKER_DATABASE_URL
    else engine
)
WorkerSessionLocal = sessionmaker(bind=worker_engine, autocommit=False, autoflush=False)
admin_engine = (
    create_engine(settings.ADMIN_DATABASE_URL, pool_pre_ping=True)
    if settings.ADMIN_DATABASE_URL
    else engine
)
AdminSessionLocal = sessionmaker(bind=admin_engine, autocommit=False, autoflush=False)
metrics_engine = (
    create_engine(settings.METRICS_DATABASE_URL, pool_pre_ping=True)
    if settings.METRICS_DATABASE_URL
    else engine
)
MetricsSessionLocal = sessionmaker(bind=metrics_engine, autocommit=False, autoflush=False)
Base = declarative_base()


def set_tenant_context(db, organization_id: str) -> None:
    """Bind PostgreSQL RLS policies to the current transaction."""

    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_admin_db():
    db = AdminSessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
