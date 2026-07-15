import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-jwt-secret-key-at-least-32-characters"
os.environ["SECRET_ENCRYPTION_KEY"] = "test-encryption-key-separate-from-jwt"
os.environ["PUBLIC_BASE_URL"] = "https://lynxpay.example.test"
os.environ["MPESA_CALLBACK_IP_ALLOWLIST"] = ""

import asyncio

import anyio.to_thread
import httpx
import pytest

from app.database import Base, SessionLocal, engine, get_db
from app.main import app


async def _run_sync_inline(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):
    return func(*args)


anyio.to_thread.run_sync = _run_sync_inline


class InlineASGIClient:
    def __init__(self, application, base_url="http://testserver"):
        self.app = application
        self.base_url = base_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def request(self, method, url, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=True)
            async with httpx.AsyncClient(transport=transport, base_url=self.base_url) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(send())

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with InlineASGIClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Acme Kenya",
            "legal_name": "Acme Kenya Limited",
            "contact_email": "owner@acme.co.ke",
            "contact_phone": "0712345678",
            "full_name": "Acme Owner",
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
