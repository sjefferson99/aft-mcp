import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from fastmcp import FastMCP
from pydantic import SecretStr

import server
from settings import Settings, SettingsError
from swagger_convert import convert

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def tool_names(spec: dict[str, Any]) -> set[str]:
    """Build a server from a spec with the real route maps and list its tools."""
    mcp = FastMCP.from_openapi(
        openapi_spec=convert(spec),
        client=httpx2.AsyncClient(base_url="https://aft.test"),
        route_maps=server.build_route_maps(),
    )
    return {tool.name for tool in asyncio.run(mcp.list_tools())}


def stub_http(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> list[httpx2.Request]:
    """Route httpx2.get through a MockTransport; return the requests seen."""
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    client = httpx2.Client(transport=httpx2.MockTransport(record))

    def fake_get(url: str, **kwargs: Any) -> httpx2.Response:
        kwargs.pop("follow_redirects", None)
        return client.get(url, follow_redirects=True, **kwargs)

    monkeypatch.setattr(httpx2, "get", fake_get)
    return seen


# --- the exposure policy ----------------------------------------------------------


def test_only_safe_reads_and_allowed_writes_become_tools(
    aft_like_spec: dict[str, Any],
) -> None:
    assert tool_names(aft_like_spec) == {
        # Reads
        "get_boards",
        "get_boards_by_board_id",
        "get_boards_columns",
        "get_cards_by_card_id",
        # Allowed writes inside a board
        "post_boards_columns",
        "patch_columns_by_column_id",
        "post_columns_cards",
        "patch_cards_by_card_id",
        "post_cards_comments",
    }


def test_never_exposes_delete_board_writes_or_side_effect_gets(
    aft_like_spec: dict[str, Any],
) -> None:
    names = tool_names(aft_like_spec)
    assert not {n for n in names if n.startswith("delete_")}
    assert "post_boards" not in names  # create a board
    assert "patch_boards_by_board_id" not in names  # board-level change
    assert "get_database_backup" not in names  # GET that creates a backup


# Reviewed snapshots of the exposure lists in server.py. Changing either list fails
# these tests until the snapshot is updated too, so a new or widened route is always a
# deliberate change that shows up in review. Update the README's "What is exposed"
# section in the same pull request.
REVIEWED_WRITES = {
    ("PATCH", r"^/api/cards/\{card_id\}$"),
    ("PATCH", r"^/api/cards/\{card_id\}/done$"),
    ("PATCH", r"^/api/cards/\{card_id\}/archive$"),
    ("PATCH", r"^/api/cards/\{card_id\}/unarchive$"),
    ("POST", r"^/api/cards/batch/archive$"),
    ("POST", r"^/api/cards/batch/unarchive$"),
    ("PUT", r"^/api/cards/\{card_id\}/assignees$"),
    ("POST", r"^/api/cards/\{card_id\}/comments$"),
    ("POST", r"^/api/cards/\{card_id\}/checklist-items$"),
    ("PATCH", r"^/api/checklist-items/\{item_id\}$"),
    ("PATCH", r"^/api/columns/\{column_id\}$"),
    ("POST", r"^/api/boards/\{board_id\}/columns$"),
    ("POST", r"^/api/columns/\{column_id\}/cards$"),
}
REVIEWED_SIDE_EFFECT_GETS = {
    r"^/api/database/backup$",
}


def test_allowed_writes_match_the_reviewed_snapshot() -> None:
    assert set(server.ALLOWED_WRITES) == REVIEWED_WRITES
    assert len(server.ALLOWED_WRITES) == len(REVIEWED_WRITES), "duplicate entry"


def test_side_effect_gets_match_the_reviewed_snapshot() -> None:
    assert set(server.SIDE_EFFECT_GET_PATTERNS) == REVIEWED_SIDE_EFFECT_GETS


@pytest.mark.parametrize(("method", "pattern"), server.ALLOWED_WRITES)
def test_each_allowed_write_is_narrow(method: str, pattern: str) -> None:
    # Rules that hold even if the snapshot above is updated carelessly.
    assert method in {"PATCH", "POST", "PUT"}, "only these write methods are allowed"
    assert pattern.startswith("^/api/"), "must be anchored at the start"
    assert pattern.endswith("$"), "must be anchored at the end"
    assert ".*" not in pattern, "must not use wildcards"
    assert ".+" not in pattern, "must not use wildcards"
    assert not pattern.startswith(r"^/api/boards/\{board_id\}$"), "board-level write"
    assert pattern != "^/api/boards$", "creating boards is not allowed"


# --- the HTTP client ---------------------------------------------------------------


def test_build_client_uses_the_settings(settings: Settings) -> None:
    client = server.build_client(settings)
    assert str(client.base_url) == "https://aft.test/"
    assert client.timeout.read == 5.0
    request = client.build_request("GET", "/api/boards")
    auth_flow = client._build_request_auth(request).auth_flow(request)
    assert next(auth_flow).headers["Authorization"].startswith("Basic ")


def test_build_client_sends_a_token_as_bearer(settings: Settings) -> None:
    with_token = settings.model_copy(
        update={
            "token": SecretStr("aft_token_value"),
            "username": None,
            "password": None,
        }
    )
    client = server.build_client(with_token)
    request = client.build_request("GET", "/api/boards")
    auth = client._build_request_auth(request)
    assert next(auth.auth_flow(request)).headers["Authorization"] == (
        "Bearer aft_token_value"
    )
    assert "aft_token_value" not in repr(auth)
    assert "aft_token_value" not in repr(vars(auth))


def test_build_auth_never_sends_a_missing_password(settings: Settings) -> None:
    incomplete = settings.model_copy(update={"password": None})
    with pytest.raises(SettingsError):
        server.build_auth(incomplete)


# --- loading the spec -------------------------------------------------------------


def test_load_spec_from_file(tmp_path: Path, aft_like_spec: dict[str, Any]) -> None:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(aft_like_spec), encoding="utf-8")
    assert server.load_spec_from_file(path) == aft_like_spec


def test_load_spec_from_url_follows_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/old":
            return httpx2.Response(302, headers={"Location": "https://aft.test/new"})
        return httpx2.Response(200, json={"swagger": "2.0"})

    seen = stub_http(monkeypatch, handler)
    assert server.load_spec_from_url("https://aft.test/old", timeout=1) == {
        "swagger": "2.0"
    }
    assert [r.url.path for r in seen] == ["/old", "/new"]


def test_load_spec_from_url_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_http(monkeypatch, lambda _: httpx2.Response(500))
    with pytest.raises(httpx2.HTTPStatusError):
        server.load_spec_from_url("https://aft.test/api/apispec.json", timeout=1)


def test_load_spec_prefers_the_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    settings: Settings,
    aft_like_spec: dict[str, Any],
) -> None:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(aft_like_spec), encoding="utf-8")
    seen = stub_http(monkeypatch, lambda _: httpx2.Response(500))
    from_file = settings.model_copy(update={"spec_file": path})
    assert server.load_spec(from_file)["openapi"] == "3.0.3"
    assert seen == []


def test_load_spec_fetches_the_url(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    aft_env: dict[str, str],
    aft_like_spec: dict[str, Any],
) -> None:
    seen = stub_http(monkeypatch, lambda _: httpx2.Response(200, json=aft_like_spec))
    assert server.load_spec(settings)["openapi"] == "3.0.3"
    assert [str(r.url) for r in seen] == [aft_env["AFT_SPEC_URL"]]


# --- assembling and running -------------------------------------------------------


def test_create_server(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    aft_like_spec: dict[str, Any],
) -> None:
    stub_http(monkeypatch, lambda _: httpx2.Response(200, json=aft_like_spec))
    mcp = server.create_server(settings)
    assert mcp.name == "AFT"
    assert "get_boards" in {t.name for t in asyncio.run(mcp.list_tools())}


def test_main_loads_the_settings_then_runs(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    calls: list[object] = []

    class FakeServer:
        def __init__(self, given: Settings) -> None:
            calls.append(given)

        def run(self) -> None:
            calls.append("run")

    monkeypatch.setattr(server, "load_settings", lambda: settings)
    monkeypatch.setattr(server, "create_server", FakeServer)
    server.main()
    assert calls == [settings, "run"]


def test_main_exits_listing_every_settings_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> Settings:
        raise SettingsError(["AFT_BASE_URL: missing", "AFT_TIMEOUT: missing"])

    monkeypatch.setattr(server, "load_settings", fail)
    with pytest.raises(SystemExit) as exited:
        server.main()
    assert "AFT_BASE_URL: missing" in str(exited.value.code)
    assert "AFT_TIMEOUT: missing" in str(exited.value.code)


def test_importing_the_module_has_no_side_effects() -> None:
    # Regression: the server used to be built at import time, which needed the
    # network and a full .env just to import it.
    assert not hasattr(server, "mcp")
