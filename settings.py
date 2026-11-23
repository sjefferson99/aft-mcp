"""Settings for the AFT MCP server, loaded and validated once at startup.

Values come from the process environment first, then from the ``.env`` file next to
this module. There are no defaults in the code for required settings: a missing or
invalid setting stops the server, and the error names every bad setting at once.
"""

from pathlib import Path
from typing import Self

from pydantic import Field, HttpUrl, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "AFT_"
ENV_FILE = Path(__file__).resolve().parent / ".env"

_TOKEN, _USER, _PASSWORD = (
    f"{ENV_PREFIX}{n}" for n in ("TOKEN", "USERNAME", "PASSWORD")
)
NO_SIGN_IN = f"set {_TOKEN}, or both {_USER} and {_PASSWORD}"
TWO_SIGN_INS = f"set {_TOKEN} or {_USER} and {_PASSWORD}, not both"


class SettingsError(RuntimeError):
    """One or more settings are missing or invalid."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        lines = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"Invalid AFT settings (set them in .env):\n{lines}")


class Settings(BaseSettings):
    """Every setting the server reads; the README's configuration table follows it."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # AFT_SPEC_FILE= in .env means unset, not "."
        extra="ignore",  # .env may hold settings for other tools
        frozen=True,
    )

    base_url: HttpUrl
    spec_url: HttpUrl | None = None
    spec_file: Path | None = None
    timeout: float = Field(gt=0, allow_inf_nan=False)
    # Sign in with an API token (sent as a Bearer token), or with a username and
    # password (sent as HTTP Basic auth). Exactly one of the two.
    token: SecretStr | None = Field(default=None, min_length=1)
    username: str | None = Field(default=None, min_length=1)
    password: SecretStr | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _needs_a_spec(self) -> Self:
        if self.spec_url is None and self.spec_file is None:
            raise ValueError(f"set {ENV_PREFIX}SPEC_URL or {ENV_PREFIX}SPEC_FILE")
        return self

    @model_validator(mode="after")
    def _needs_one_way_to_sign_in(self) -> Self:
        basic = (self.username, self.password)
        if self.token is not None:
            if basic != (None, None):
                raise ValueError(TWO_SIGN_INS)
        elif None in basic:
            raise ValueError(NO_SIGN_IN)
        return self


def _describe(error: ValidationError) -> list[str]:
    """One line per problem, naming the variable; input values are left out."""
    problems = []
    for detail in error.errors(include_input=False):
        message = detail["msg"].removeprefix("Value error, ")
        if detail["loc"]:
            name = f"{ENV_PREFIX}{str(detail['loc'][0]).upper()}"
            message = f"{name}: {message}"
        problems.append(message)
    return problems


def load_settings(env_file: Path | None = ENV_FILE) -> Settings:
    """Read and validate every setting.

    Args:
        env_file: The ``.env`` file to read after the environment, or None to read
            the environment only (as the tests do, so a developer's ``.env`` never
            applies).

    Raises:
        SettingsError: Naming every missing or invalid setting.
    """
    try:
        return Settings(_env_file=env_file)
    except ValidationError as error:
        raise SettingsError(_describe(error)) from None
