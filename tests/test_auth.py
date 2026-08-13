from __future__ import annotations

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from backend.app.api.routes import auth as auth_routes
from backend.app.core import security
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import dof_auth
from backend.app.services.dof_auth import (
    DofLoginResult,
    extract_observer_name,
    normalize_obserkode,
)


class FakeSession:
    """Minimal async session stand-in for the auth routes."""

    def __init__(self, users: dict[str, User]):
        self.users = users
        self.pending: list[User] = []

    async def get(self, _model, key):
        return self.users.get(key)

    def add(self, instance):
        self.pending.append(instance)

    async def commit(self):
        for instance in self.pending:
            self.users[instance.obserkode] = instance
        self.pending.clear()

    async def refresh(self, _instance):
        return None


@pytest.fixture
def client():
    users: dict[str, User] = {}
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(auth_routes.router, prefix="/api/v1")

    @app.get("/api/v1/protected")
    async def protected_route(user: User = Depends(security.require_user)) -> dict:
        return {"obserkode": user.obserkode}

    app.dependency_overrides[get_db] = lambda: FakeSession(users)
    security._login_attempts.clear()
    with TestClient(app) as test_client:
        test_client.users = users
        yield test_client


def _stub_login(monkeypatch, navn="Test Observatør"):
    async def fake_authenticate(obserkode: str, password: str) -> DofLoginResult:
        assert password
        return DofLoginResult(obserkode=obserkode, token="tok", navn=navn)

    monkeypatch.setattr(auth_routes, "authenticate_dof_user", fake_authenticate)


def test_normalize_obserkode_uppercases_and_validates():
    assert normalize_obserkode(" abc12 ") == "ABC12"
    for invalid in ["", "A", "abc-12", "A" * 17]:
        with pytest.raises(ValueError):
            normalize_obserkode(invalid)


def test_extract_observer_name_reads_dofbasen_markup():
    html = 'x<td>Navn</acronym>:</td><td valign="top">Anders And</td>y'
    assert extract_observer_name(html) == "Anders And"
    assert extract_observer_name("ingen navn her") == ""


async def test_authenticate_dof_user_uses_legacy_endpoint_and_token(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"token": "abc123"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(dof_auth, "fetch_observer_name", lambda kode: _async_value(""))

    result = await dof_auth.authenticate_dof_user("abc12", "hemmelig")
    assert seen["url"] == "https://krydslister.dofbasen.dk/api/v1/login"
    assert result.token == "abc123"
    assert result.obserkode == "ABC12"


async def _async_value(value):
    return value


async def test_fetch_observer_name_decodes_latin1(monkeypatch):
    html = 'Navn</acronym>:</td><td valign="top">Christian Helligsø</td>'.encode("latin-1")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=html))
    original = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    assert await dof_auth.fetch_observer_name("ABC12") == "Christian Helligsø"


def test_protected_endpoint_requires_login(client):
    assert client.get("/api/v1/protected").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


def test_login_creates_user_and_opens_session(client, monkeypatch):
    _stub_login(monkeypatch)
    response = client.post(
        "/api/v1/auth/login", json={"obserkode": "abc12", "adgangskode": "hemmelig"}
    )
    assert response.status_code == 200
    assert response.json()["obserkode"] == "ABC12"
    assert response.json()["navn"] == "Test Observatør"
    assert "ABC12" in client.users

    assert client.get("/api/v1/protected").json() == {"obserkode": "ABC12"}
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True

    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/protected").status_code == 401


def test_login_rejects_invalid_obserkode(client):
    response = client.post(
        "/api/v1/auth/login", json={"obserkode": "ab-12", "adgangskode": "hemmelig"}
    )
    assert response.status_code == 400


def test_login_is_rate_limited_after_five_attempts(client, monkeypatch):
    async def failing_authenticate(obserkode: str, password: str):
        raise auth_routes.HTTPException(status_code=401, detail="DOFbasen-login fejlede.")

    monkeypatch.setattr(auth_routes, "authenticate_dof_user", failing_authenticate)
    payload = {"obserkode": "ABC12", "adgangskode": "forkert"}
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    assert client.post("/api/v1/auth/login", json=payload).status_code == 429
