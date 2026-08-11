"""V7: Product-owned stock reserve / release."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.product import models as _product_models  # noqa: F401
from app.product.models import Product
from app.product.schemas import ProductCreate
from app.product import service


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[Product.__table__])
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_reserve_decrements_stock_and_returns_prices(db, monkeypatch):
    monkeypatch.setattr(service, "cache_delete", lambda *_: None)
    product = service.create_product(
        db, ProductCreate(name="Widget", price=12.5, stock_quantity=10)
    )

    reserved = service.reserve_stock(db, [(product.id, 3)])

    assert reserved == [
        {"product_id": product.id, "quantity": 3, "unit_price": 12.5}
    ]
    db.refresh(product)
    assert product.stock_quantity == 7


def test_reserve_rejects_insufficient_stock(db, monkeypatch):
    monkeypatch.setattr(service, "cache_delete", lambda *_: None)
    product = service.create_product(
        db, ProductCreate(name="Widget", price=5.0, stock_quantity=1)
    )

    with pytest.raises(Exception) as exc:
        service.reserve_stock(db, [(product.id, 5)])

    assert exc.value.status_code == 409
    db.refresh(product)
    assert product.stock_quantity == 1


def test_release_restores_stock(db, monkeypatch):
    monkeypatch.setattr(service, "cache_delete", lambda *_: None)
    product = service.create_product(
        db, ProductCreate(name="Widget", price=5.0, stock_quantity=10)
    )
    service.reserve_stock(db, [(product.id, 4)])
    service.release_stock(db, [(product.id, 4)])

    db.refresh(product)
    assert product.stock_quantity == 10
