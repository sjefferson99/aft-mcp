from pathlib import Path

import pytest

import settings as settings_module
from settings import SettingsError, load_settings

ALL_SETTINGS = [
    "AFT_BASE_URL",
    "AFT_SPEC_URL",
    "AFT_SPEC_FILE",
    "AFT_TIMEOUT",
    "AFT_TOKEN",
    "AFT_USERNAME",
    "AFT_PASSWORD",
]


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ALL_SETTINGS:
        monkeypatch.delenv(name, raising=False)


def problems(env_file: Path | None = None) -> list[str]:
    with pytest.raises(SettingsError) as raised:
        load_settings(env_file=env_file)
    return raised.value.problems


def test_loads_every_setting_from_the_environment(aft_env: dict[str, str]) -> None:
    loaded = load_settings(env_file=None)
    assert str(loaded.base_url) == "https://aft.test/"
    assert str(loaded.spec_url) == aft_env["AFT_SPEC_URL"]
    assert loaded.spec_file is None
    assert loaded.timeout == 5.0
    assert loaded.token is None
    assert loaded.username == "agent@example.com"
    assert loaded.password is not None
    assert loaded.password.get_secret_value() == "secret"


@pytest.mark.usefixtures("no_env")
def test_reads_the_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    lines = [
        "AFT_BASE_URL=https://aft.test",
        "AFT_SPEC_FILE=spec.json",
        "AFT_TIMEOUT=2",
        "AFT_USERNAME=agent@example.com",
        "AFT_PASSWORD=secret",
        "OTHER_TOOL=1",  # settings for other tools are ignored
    ]
    env_file.write_text("\n".join(lines), encoding="utf-8")
    loaded = load_settings(env_file=env_file)
    assert loaded.spec_file is not None
    assert loaded.spec_file.name == "spec.json"
    assert loaded.spec_url is None


def test_the_environment_wins_over_the_env_file(
    aft_env: dict[str, str], tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AFT_TIMEOUT=99\n", encoding="utf-8")
    assert load_settings(env_file=env_file).timeout == 5.0


def test_reads_the_env_file_next_to_the_module_by_default() -> None:
    module_dir = Path(settings_module.__file__).resolve().parent
    assert module_dir / ".env" == settings_module.ENV_FILE


@pytest.mark.usefixtures("no_env")
def test_reports_every_missing_setting_at_once() -> None:
    assert problems() == [
        "AFT_BASE_URL: Field required",
        "AFT_TIMEOUT: Field required",
    ]


def test_needs_a_spec_url_or_file(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.delenv("AFT_SPEC_URL")
    assert problems() == ["set AFT_SPEC_URL or AFT_SPEC_FILE"]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "soon"])
def test_rejects_a_bad_timeout(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], value: str
) -> None:
    monkeypatch.setenv("AFT_TIMEOUT", value)
    (problem,) = problems()
    assert problem.startswith("AFT_TIMEOUT: ")


def test_rejects_a_base_url_that_is_not_http(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_BASE_URL", "aft.test")
    (problem,) = problems()
    assert problem.startswith("AFT_BASE_URL: ")


def test_an_empty_value_counts_as_unset(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_SPEC_FILE", "")
    assert load_settings(env_file=None).spec_file is None
    monkeypatch.setenv("AFT_PASSWORD", "")
    assert problems() == ["set AFT_TOKEN, or both AFT_USERNAME and AFT_PASSWORD"]


# --- signing in -------------------------------------------------------------------


@pytest.fixture
def token_env(monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]) -> None:
    """Sign in with a token instead of the username and password from ``aft_env``."""
    monkeypatch.delenv("AFT_USERNAME")
    monkeypatch.delenv("AFT_PASSWORD")
    monkeypatch.setenv("AFT_TOKEN", "aft_token_value")


@pytest.mark.usefixtures("token_env")
def test_loads_a_token_instead_of_a_username_and_password() -> None:
    loaded = load_settings(env_file=None)
    assert loaded.token is not None
    assert loaded.token.get_secret_value() == "aft_token_value"
    assert loaded.username is None
    assert loaded.password is None


@pytest.mark.usefixtures("token_env")
@pytest.mark.parametrize("name", ["AFT_USERNAME", "AFT_PASSWORD"])
def test_a_token_and_a_username_or_password_is_an_error(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "x")
    assert problems() == ["set AFT_TOKEN or AFT_USERNAME and AFT_PASSWORD, not both"]


@pytest.mark.parametrize("name", ["AFT_USERNAME", "AFT_PASSWORD"])
def test_a_username_needs_a_password_and_the_other_way_round(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], name: str
) -> None:
    monkeypatch.delenv(name)
    assert problems() == ["set AFT_TOKEN, or both AFT_USERNAME and AFT_PASSWORD"]


@pytest.mark.usefixtures("token_env")
def test_the_token_never_appears_in_output(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = load_settings(env_file=None)
    assert "aft_token_value" not in repr(loaded)
    assert "aft_token_value" not in str(loaded)
    monkeypatch.setenv("AFT_USERNAME", "agent@example.com")
    with pytest.raises(SettingsError) as raised:
        load_settings(env_file=None)
    assert "aft_token_value" not in str(raised.value)


def test_the_password_never_appears_in_output(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    loaded = load_settings(env_file=None)
    assert "secret" not in repr(loaded)
    assert "secret" not in str(loaded)
    monkeypatch.setenv("AFT_TIMEOUT", "never")
    with pytest.raises(SettingsError) as raised:
        load_settings(env_file=None)
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_the_error_message_lists_each_problem() -> None:
    error = SettingsError(["AFT_A: bad", "AFT_B: bad"])
    assert str(error) == (
        "Invalid AFT settings (set them in .env):\n  - AFT_A: bad\n  - AFT_B: bad"
    )
