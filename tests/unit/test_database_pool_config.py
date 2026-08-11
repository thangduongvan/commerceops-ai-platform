"""V2 (Horizontal Scaling): DB connection pool must be explicit and
configurable, since the real ceiling is `running_tasks * (pool_size +
max_overflow)` against RDS's connection budget (see ADR-003)."""

from sqlalchemy import create_engine

from app.core.config import Settings
from app.core.database import engine


def test_settings_have_sane_pool_defaults():
    settings = Settings()

    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 3
    # V6: pool_recycle bounds how long a pre-failover connection can linger.
    assert settings.db_pool_recycle_seconds == 300


def test_module_engine_uses_configured_pool_size():
    # Constructing the engine doesn't open a connection, so this is safe to
    # assert without a live database.
    assert engine.pool.size() == 5


def test_engine_pool_size_is_actually_driven_by_settings():
    settings = Settings(db_pool_size=2, db_max_overflow=1)

    test_engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )

    assert test_engine.pool.size() == 2
    assert test_engine.pool._max_overflow == 1
