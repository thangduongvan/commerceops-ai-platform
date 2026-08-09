import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app

# Import models so all tables are registered on Base.metadata.
from app.customer import models as _customer_models  # noqa: F401
from app.order import models as _order_models  # noqa: F401
from app.product import models as _product_models  # noqa: F401

# In-memory SQLite shared across connections via StaticPool. This keeps
# integration tests fast and dependency-free on any OS; the Postgres
# container in docker-compose.yml remains the real deployment target.
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def _reset_database():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client():
    # Not used as a context manager on purpose: it avoids triggering the
    # app's lifespan (which calls create_all against the real Postgres
    # engine), so tests never need a live database connection.
    return TestClient(app)
