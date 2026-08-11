"""V6 (Database HA): read engine aliasing, pool_recycle, read retry, primary fallback."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.core import database as database_module
from app.core.config import settings
from app.product import service as product_service


def test_read_engine_aliases_write_engine_when_replica_disabled():
    # Default settings: read_replica_enabled=False, no DATABASE_READ_URL.
    # The module-level read_engine must be the same object as engine so
    # get_read_db and get_db are interchangeable in tests / single-DB runs.
    assert settings.read_replica_enabled is False
    assert database_module.read_engine is database_module.engine


def test_pool_recycle_and_pre_ping_are_configured():
    pool = database_module.engine.pool
    assert database_module.engine.pool._pre_ping is True
    # pool_recycle is stored on the pool; SQLAlchemy exposes it as _recycle.
    assert pool._recycle == settings.db_pool_recycle_seconds


def test_resolve_read_engine_builds_separate_engine_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "read_replica_enabled", True)
    monkeypatch.setattr(
        settings,
        "database_read_url",
        "postgresql+psycopg://u:p@replica:5432/commerceops",
    )
    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql+psycopg://u:p@primary:5432/commerceops",
    )

    # Don't actually connect — just confirm a distinct engine object is built.
    created = []
    real_build = database_module._build_engine

    def _fake_build(url):
        eng = MagicMock(name=f"engine({url})")
        eng.url = url
        created.append(url)
        return eng

    monkeypatch.setattr(database_module, "_build_engine", _fake_build)
    resolved = database_module._resolve_read_engine()
    assert resolved.url.endswith("@replica:5432/commerceops")
    assert created == ["postgresql+psycopg://u:p@replica:5432/commerceops"]
    # Restore (not strictly needed — function-scoped monkeypatch).
    monkeypatch.setattr(database_module, "_build_engine", real_build)


def test_with_read_retry_retries_operational_errors(monkeypatch):
    monkeypatch.setattr(settings, "db_transient_retry_attempts", 3)
    sleeps: list[float] = []
    monkeypatch.setattr(database_module.time, "sleep", sleeps.append)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError("statement", {}, Exception("connection reset"))
        return "ok"

    assert database_module.with_read_retry(flaky) == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_with_read_retry_exhausts_and_reraises(monkeypatch):
    monkeypatch.setattr(settings, "db_transient_retry_attempts", 2)
    monkeypatch.setattr(database_module.time, "sleep", lambda *_: None)

    def always_fail():
        raise OperationalError("statement", {}, Exception("down"))

    with pytest.raises(OperationalError):
        database_module.with_read_retry(always_fail)


def test_product_read_falls_back_to_primary_and_logs(monkeypatch, caplog):
    """Replica connection OperationalError → re-run against SessionLocal."""
    import logging

    caplog.set_level(logging.WARNING)

    class BoomSession:
        def get(self, *args, **kwargs):
            raise OperationalError(
                "statement",
                {},
                Exception("could not connect to server: Connection refused"),
            )

    class PrimaryProduct:
        id = 1
        name = "Widget"
        description = None
        price = 1.0
        stock_quantity = 5
        created_at = None
        updated_at = None

    class PrimarySession:
        def get(self, model, product_id):
            return PrimaryProduct()

        def close(self):
            pass

    monkeypatch.setattr(product_service, "cache_get_json", lambda *_: None)
    monkeypatch.setattr(product_service, "cache_set_json", lambda *a, **k: None)
    monkeypatch.setattr(product_service, "with_read_retry", lambda fn: fn())
    monkeypatch.setattr(product_service, "SessionLocal", lambda: PrimarySession())

    # Avoid ProductRead.model_validate needing a real datetime by stubbing serialize.
    monkeypatch.setattr(
        product_service,
        "_serialize",
        lambda p: {
            "id": p.id,
            "name": p.name,
            "description": None,
            "price": p.price,
            "stock_quantity": p.stock_quantity,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2020-01-01T00:00:00Z",
        },
    )

    result = product_service.get_product(BoomSession(), product_id=1)
    assert result.id == 1
    assert any("read_replica_unavailable" in r.message for r in caplog.records)


def test_product_read_does_not_fall_back_on_statement_errors(monkeypatch):
    """A non-connection OperationalError (e.g. no such table) must surface."""

    class BoomSession:
        def get(self, *args, **kwargs):
            raise OperationalError("statement", {}, Exception("no such table: products"))

    monkeypatch.setattr(product_service, "cache_get_json", lambda *_: None)
    monkeypatch.setattr(product_service, "with_read_retry", lambda fn: fn())

    with pytest.raises(OperationalError, match="no such table"):
        product_service.get_product(BoomSession(), product_id=1)


def test_replica_lag_returns_none_when_replica_disabled():
    assert settings.read_replica_enabled is False
    assert database_module.replica_lag_seconds() is None
