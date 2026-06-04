from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dotenv import dotenv_values
from platformdirs import user_data_dir
from pydantic import BaseModel, Field

APP_NAME = "noc-cli"

# Config field -> environment variable / .env key
_FIELD_ENV: dict[str, str] = {
    "zendesk_subdomain": "ZENDESK_SUBDOMAIN",
    "zendesk_email": "ZENDESK_EMAIL",
    "zendesk_api_token": "ZENDESK_API_TOKEN",
    "tickets_root": "NOC_TICKETS_ROOT",
    "owner": "NOC_OWNER",
    "watch_view": "NOC_WATCH_VIEW",
    "watch_assignee": "NOC_WATCH_ASSIGNEE",
    "notify": "NOC_NOTIFY",
    "timezone": "NOC_TZ",
}


def data_dir() -> Path:
    """noc-cli data directory. `NOC_HOME` overrides the platform default."""
    override = os.environ.get("NOC_HOME")
    if override:
        return Path(override).expanduser()
    return Path(user_data_dir(APP_NAME, appauthor=False))


def config_path() -> Path:
    return data_dir() / ".env"


def db_path() -> Path:
    return data_dir() / "noc.db"


def valid_config_keys() -> list[str]:
    """Editable Config field names accepted by `noc-cli config`."""
    return list(_FIELD_ENV)


def set_config_value(key: str, value: str) -> None:
    """Set one persisted config value in the data-dir `.env` file.

    This intentionally reads only the `.env` file, not process environment
    overrides, so `config set` does not accidentally persist transient shell
    values.
    """
    if key not in _FIELD_ENV:
        valid = ", ".join(valid_config_keys())
        raise KeyError(f"Unknown config key {key!r}. Valid keys: {valid}")

    # Import here to avoid a module-level cycle: setup imports Config.
    from noc_cli.setup import build_env_lines

    path = config_path()
    file_values = dotenv_values(path)
    answers: dict[str, str] = {}

    for field, env_key in _FIELD_ENV.items():
        if field == key:
            answers[env_key] = value
            continue
        file_value = file_values.get(env_key)
        if file_value is not None:
            answers[env_key] = file_value

    payload = build_env_lines(answers)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            encoding="utf-8",
            delete=False,
        ) as tmp:
            temp_path = Path(tmp.name)
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        temp_path.replace(path)
    except Exception:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
        raise


class Config(BaseModel):
    zendesk_subdomain: str = ""
    zendesk_email: str = ""
    zendesk_api_token: str = ""
    tickets_root: Path = Field(default_factory=lambda: Path.cwd() / "Tickets")
    owner: str = Field(default_factory=lambda: os.environ.get("USER", "unknown"))
    watch_view: str = ""
    watch_assignee: str = ""
    notify: str = "banner,ping"
    timezone: str = "local"  # "local" (system), "utc", or an IANA name e.g. America/New_York

    @property
    def zendesk_base_url(self) -> str:
        return f"https://{self.zendesk_subdomain}.zendesk.com/api/v2"


def load_config() -> Config:
    """Load config from the data-dir `.env`, with process env overriding file values."""
    path = config_path()
    file_values = dotenv_values(path) if path.exists() else {}
    merged: dict[str, str] = {}
    for field, env_key in _FIELD_ENV.items():
        value = os.environ.get(env_key, file_values.get(env_key))
        if value:
            merged[field] = value
    return Config(**merged)
