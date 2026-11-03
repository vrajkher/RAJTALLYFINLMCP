"""Administrator policy: an immutable ceiling the assistant cannot change.

Settings (``config``) are preferences the user or assistant may edit. Policy is set once, at process
start, by whoever launches the server (an administrator, or an embedding gateway such as RAJ Finance
Agent), and nothing reachable through MCP can widen it:

* ``locked``          settings file ignored; host, port and company cannot be changed at run time
* ``company``         with ``locked``, every request is pinned to this company (explicit or blank names too)
* ``company_guid``    optional: before every write the open company with that name must have this GUID
* ``access_ceiling``  the highest access level allowed (read_only < read_write < full)
* ``tool_profile``    which tools are registered at all: read | post | write | all
* ``allow_tools``     optional allow-list intersected with the profile

Environment: RAJTALLY_LOCK=1, TALLY_COMPANY, RAJTALLY_COMPANY_GUID, RAJTALLY_ACCESS_CEILING,
RAJTALLY_TOOL_PROFILE, RAJTALLY_ALLOW_TOOLS (comma separated). The command line sets the same values.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

ACCESS_ORDER = ("read_only", "read_write", "full")
TOOL_PROFILES = ("read", "post", "write", "all")

# Tools that run arbitrary Tally queries or payloads: never part of the read / post / write profiles.
ADVANCED_TOOLS = frozenset({"tally_sql", "tally_odbc_tables", "tally_tdl_collection", "tally_evaluate",
                            "tally_raw_xml", "tally_import_xml"})
# Tools that change which company or settings are used: never available when locked.
SWITCHING_TOOLS = frozenset({"tally_use_company", "tally_settings_update"})
# The post profile: read tools plus creating accounting vouchers only.
POST_TOOLS = frozenset({"tally_create_voucher"})


class PolicyError(Exception):
    pass


@dataclass(frozen=True)
class Policy:
    locked: bool = False
    company: str = ""
    company_guid: str = ""
    access_ceiling: str = "full"
    tool_profile: str = "all"
    allow_tools: frozenset[str] | None = None

    def clamp_access(self, access: str) -> str:
        if ACCESS_ORDER.index(access) > ACCESS_ORDER.index(self.access_ceiling):
            return self.access_ceiling
        return access

    def allows_access(self, access: str) -> bool:
        return ACCESS_ORDER.index(access) <= ACCESS_ORDER.index(self.access_ceiling)

    def tool_allowed(self, name: str, read_only: bool, destructive: bool) -> bool:
        if self.allow_tools is not None and name not in self.allow_tools:
            return False
        if self.locked and name in SWITCHING_TOOLS:
            return False
        profile = self.tool_profile
        if profile == "all":
            return True
        if name in ADVANCED_TOOLS:
            return False
        if profile == "read":
            return read_only
        if profile == "post":
            return read_only or name in POST_TOOLS
        # write: everything except advanced, switching and destructive tools
        return (read_only or not destructive) and name not in SWITCHING_TOOLS

    def company_for(self, requested: str | None) -> str | None:
        """The company a request may use. Locked: always the pinned company; another name is refused."""
        if not (self.locked and self.company):
            return requested
        if requested and requested.strip().lower() != self.company.lower():
            raise PolicyError(f"This server is locked to company '{self.company}'; '{requested}' is not allowed.")
        return self.company


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def from_env() -> Policy:
    ceiling = os.environ.get("RAJTALLY_ACCESS_CEILING", "full").strip() or "full"
    profile = os.environ.get("RAJTALLY_TOOL_PROFILE", "all").strip() or "all"
    if ceiling not in ACCESS_ORDER:
        raise PolicyError(f"RAJTALLY_ACCESS_CEILING must be one of {', '.join(ACCESS_ORDER)}")
    if profile not in TOOL_PROFILES:
        raise PolicyError(f"RAJTALLY_TOOL_PROFILE must be one of {', '.join(TOOL_PROFILES)}")
    allow = os.environ.get("RAJTALLY_ALLOW_TOOLS", "").strip()
    locked = _truthy(os.environ.get("RAJTALLY_LOCK"))
    company = os.environ.get("TALLY_COMPANY", "").strip() if locked else ""
    if locked and not company:
        raise PolicyError("A locked server needs a company (TALLY_COMPANY or --company).")
    return Policy(locked=locked, company=company,
                  company_guid=os.environ.get("RAJTALLY_COMPANY_GUID", "").strip() if locked else "",
                  access_ceiling=ceiling, tool_profile=profile,
                  allow_tools=frozenset(t.strip() for t in allow.split(",") if t.strip()) if allow else None)


_lock = threading.Lock()
_current: Policy | None = None


def get() -> Policy:
    global _current
    with _lock:
        if _current is None:
            _current = from_env()
        return _current


def reset() -> None:
    """Forget the cached policy (tests and process start only; not reachable through MCP)."""
    global _current
    with _lock:
        _current = None
