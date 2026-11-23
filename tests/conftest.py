from typing import Any

import pytest

from settings import Settings, load_settings

ENV = {
    "AFT_BASE_URL": "https://aft.test",
    "AFT_SPEC_URL": "https://aft.test/api/apispec.json",
    "AFT_TIMEOUT": "5",
    "AFT_USERNAME": "agent@example.com",
    "AFT_PASSWORD": "secret",
}


@pytest.fixture
def aft_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set every required AFT_* setting (signing in with a username and password),
    and make sure AFT_SPEC_FILE and AFT_TOKEN are unset."""
    monkeypatch.delenv("AFT_SPEC_FILE", raising=False)
    monkeypatch.delenv("AFT_TOKEN", raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    return ENV


@pytest.fixture
def settings(aft_env: dict[str, str]) -> Settings:
    """Settings loaded from ``aft_env`` alone, never from a ``.env`` file."""
    return load_settings(env_file=None)


def _op(**extra: Any) -> dict[str, Any]:
    return {"responses": {"200": {"description": "OK"}}, **extra}


CARD_ID = {"name": "card_id", "in": "path", "required": True, "type": "integer"}
BOARD_ID = {"name": "board_id", "in": "path", "required": True, "type": "integer"}
COLUMN_ID = {"name": "column_id", "in": "path", "required": True, "type": "integer"}
CARD_UPDATE = {
    "name": "body",
    "in": "body",
    "required": True,
    "schema": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "done": {"type": "boolean"}},
    },
}


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests (marked ``anyio``) on asyncio only."""
    return "asyncio"


@pytest.fixture
def aft_like_spec() -> dict[str, Any]:
    """A small Swagger 2.0 spec shaped like AFT's, with safe and unsafe routes.

    Every route here is one the allowlist either must or must not expose, so the
    server tests can assert on the exact set of tools built from it.
    """
    return {
        "swagger": "2.0",
        "info": {"title": "AFT", "version": "1"},
        "host": "aft.test",
        "basePath": "/",
        "schemes": ["https"],
        "paths": {
            "/api/boards": {"get": _op(), "post": _op()},
            "/api/boards/{board_id}": {
                "parameters": [BOARD_ID],
                "get": _op(),
                "patch": _op(),  # board-level rename: must not be exposed
                "delete": _op(),
            },
            "/api/boards/{board_id}/columns": {
                "parameters": [BOARD_ID],
                "get": _op(),
                "post": _op(),
            },
            "/api/columns/{column_id}": {
                "parameters": [COLUMN_ID],
                "patch": _op(),
                "delete": _op(),
            },
            "/api/columns/{column_id}/cards": {
                "parameters": [COLUMN_ID],
                "post": _op(),
            },
            "/api/cards/{card_id}": {
                "parameters": [CARD_ID],
                "get": _op(),
                "patch": _op(parameters=[CARD_UPDATE]),
                "delete": _op(),
            },
            "/api/cards/{card_id}/comments": {
                "parameters": [CARD_ID],
                "post": _op(),
            },
            "/api/database/backup": {"get": _op()},  # GET with a side effect
        },
    }
