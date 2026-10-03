"""Settings for the Tally connection.

Values come from three places, later ones win:

1. built-in defaults
2. environment variables (``TALLY_HOST`` etc.)
3. the saved settings file (``~/.rajtally/settings.json``), which is what the
   ``tally_settings_update`` tool writes.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

ACCESS_LEVELS = ("read_only", "read_write", "full")


@dataclass
class TallyConfig:
    host: str = "localhost"
    port: int = 9000
    company: str = ""  # blank = whichever company is active in Tally
    access: str = "read_write"  # read_only | read_write | full (full also allows delete/cancel)
    odbc_dsn: str = ""  # blank = build a DSN-less connection string from host/port
    timeout: int = 60  # seconds per request
    encoding: str = "utf-16"  # utf-16 handles Gujarati/Hindi names; utf-8 also accepted
    gst_schema: str = "prime"  # prime (TallyPrime 3+) | erp9 (Tally.ERP 9 / older Prime)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


_ENV = {
    "host": "TALLY_HOST",
    "port": "TALLY_PORT",
    "company": "TALLY_COMPANY",
    "access": "TALLY_ACCESS",
    "odbc_dsn": "TALLY_ODBC_DSN",
    "timeout": "TALLY_TIMEOUT",
    "encoding": "TALLY_ENCODING",
    "gst_schema": "TALLY_GST_SCHEMA",
}

_lock = threading.Lock()
_current: TallyConfig | None = None


def settings_path() -> Path:
    return Path(os.environ.get("RAJTALLY_SETTINGS", Path.home() / ".rajtally" / "settings.json"))


def _coerce(name: str, value: Any) -> Any:
    kind = {f.name: f.type for f in fields(TallyConfig)}[name]
    if kind == "int":
        return int(value)
    return str(value).strip()


def _validate(cfg: TallyConfig) -> TallyConfig:
    if cfg.access not in ACCESS_LEVELS:
        raise ValueError(f"access must be one of {', '.join(ACCESS_LEVELS)}")
    if cfg.gst_schema not in ("prime", "erp9"):
        raise ValueError("gst_schema must be 'prime' or 'erp9'")
    if not 1 <= cfg.port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if cfg.timeout < 1:
        raise ValueError("timeout must be at least 1 second")
    return cfg


def load() -> TallyConfig:
    """Build the config from defaults, environment and the saved file."""
    cfg = TallyConfig()
    for name, env in _ENV.items():
        if os.environ.get(env):
            setattr(cfg, name, _coerce(name, os.environ[env]))
    path = settings_path()
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        for name in _ENV:
            if name in saved:
                setattr(cfg, name, _coerce(name, saved[name]))
    return _validate(cfg)


def get() -> TallyConfig:
    global _current
    with _lock:
        if _current is None:
            _current = load()
        return _current


def update(changes: dict[str, Any], persist: bool = True) -> TallyConfig:
    """Apply changes, validate, and (by default) save them to the settings file."""
    global _current
    with _lock:
        base = _current or load()
        data = asdict(base)
        for name, value in changes.items():
            if name not in data:
                raise ValueError(f"Unknown setting: {name}")
            data[name] = _coerce(name, value)
        cfg = _validate(TallyConfig(**data))
        if persist:
            path = settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
        _current = cfg
        return cfg


def reset() -> None:
    """Forget the cached config (used by tests)."""
    global _current
    with _lock:
        _current = None
