import time
import uuid
import pytest
from fastapi.testclient import TestClient
from portfolio_advisor.api import create_app
from portfolio_advisor.config import Settings
from portfolio_advisor.db import Repository, Portfolio
from portfolio_advisor.storage import LocalStore


@pytest.fixture
def services(tmp_path):
    settings = Settings({"DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}", "AUTH_SESSION_SECRET": "test-only-session-secret-0000000000000",
                         "APP_ORIGIN": "http://testserver", "APP_ENV": "local"}, rehearsal=True)
    repository = Repository(settings.get("DATABASE_URL"))
    repository.initialize()
    store = LocalStore(tmp_path / "objects")
    return settings, repository, store


@pytest.fixture
def app(services):
    return create_app(*services)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        client.get("/auth/login")
        csrf = client.get("/api/me").json()["csrf"]
        client.headers["x-csrf-token"] = csrf
        yield client


def add_portfolio(repository, owner="user-a", holdings=None):
    pid = str(uuid.uuid4())
    with repository.session.begin() as db:
        db.add(Portfolio(id=pid, owner=owner, name="Test portfolio", created=time.time(),
                         holdings=holdings or [{"symbol": "AAPL", "quantity": "10", "currency": "USD"}]))
    return pid
