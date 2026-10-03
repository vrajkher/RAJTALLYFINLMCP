"""The MCP server: wires the tool modules together and adds the OpenAI MCP extensions.

OpenAI extensions used (https://github.com/openai/mcp-extensions):
  * Structured settings  - host, port, company, access level ... editable from the client's settings UI
                           (tools: tally_settings_read / tally_settings_update)
  * Composer mentions    - type @ and search ledgers, groups and stock items by name
Clients that do not know these extensions simply see ordinary MCP tools and resources.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Literal
from urllib.parse import quote, unquote

from mcp.server.extension import ToolBinding
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp_types import ResourceLink
from openai_mcp_extensions import (
    OpenAIExtensions,
    OpenAIMentionSearchParams,
    OpenAIMentionSearchResult,
    OpenAISettings,
    OpenAISettingsGroup,
    OpenAISettingsProperty,
)
from pydantic import BaseModel, ConfigDict, Field

from . import __version__, config, core
from . import tally_xml as tx
from .tools import advanced, compliance, connection, gst, inventory, masters, reports, vouchers

INSTRUCTIONS = (
    "Tools for Tally.ERP 9 / TallyPrime accounting data. Call tally_guide once for the full map. "
    "Typical flow: tally_status -> tally_use_company -> read / report -> write. "
    "Use tally_search to get exact names before any tool that takes a ledger, group or item name. "
    "Before any write, run the same call with dry_run=true and show the user what will be posted. "
    "Dates are YYYY-MM-DD; blank dates mean the current Indian financial year (April-March)."
)


# --------------------------------------------------------------------------
# OpenAI extension: structured settings
# --------------------------------------------------------------------------

class TallySettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    host: str = Field(title="Tally host", description="Computer running Tally. Usually localhost.")
    port: int = Field(title="Tally port", description="Tally's HTTP port. Default 9000.")
    company: str = Field(title="Company", description="Exact company name. Blank = the company active in Tally.")
    access: Literal["read_only", "read_write", "full"] = Field(
        title="Access level",
        description="read_only: no changes. read_write: create and edit. full: also delete and cancel.")
    odbc_dsn: str = Field(alias="odbcDsn", title="ODBC data source",
                          description="Optional DSN, e.g. TallyODBC64_9000. Blank = auto-detect.")
    timeout: int = Field(title="Timeout (seconds)", description="How long to wait for Tally to answer.")
    gst_schema: Literal["prime", "erp9"] = Field(
        alias="gstSchema", title="Tally version",
        description="prime: TallyPrime 3 or later. erp9: Tally.ERP 9 or older TallyPrime.")


def _settings_values() -> TallySettings:
    cfg = config.get()
    return TallySettings(host=cfg.host, port=cfg.port, company=cfg.company, access=cfg.access,
                         odbc_dsn=cfg.odbc_dsn, timeout=cfg.timeout, gst_schema=cfg.gst_schema)


_DESCRIPTIONS = {
    "tally_settings_read": (
        "Show the connection settings: Tally host and port, selected company, access level "
        "(read_only / read_write / full), ODBC data source, timeout and Tally version."),
    "tally_settings_update": (
        "Change connection settings and save them. Pass only what should change inside 'set', e.g. "
        '{"set": {"access": "full"}} to allow delete and cancel, {"set": {"port": 9001}}, '
        '{"set": {"gstSchema": "erp9"}} for Tally.ERP 9. Keys: host, port, company, access, odbcDsn, '
        "timeout, gstSchema."),
    "search_mentions": (
        "Used by the app's @-mention picker: finds ledgers, groups and stock items whose name contains "
        "the query. Assistants should use tally_search instead."),
}


def _described(bindings: Sequence[ToolBinding]) -> tuple[ToolBinding, ...]:
    """The extension SDK registers its tools without descriptions; add them so any client can use them."""
    out = []
    for binding in bindings:
        name = binding.kwargs.get("name") or binding.fn.__name__
        out.append(replace(binding, kwargs={**binding.kwargs, "description": _DESCRIPTIONS[name]}))
    return tuple(out)


class Settings(OpenAISettings[TallySettings]):
    def tools(self) -> Sequence[ToolBinding]:
        return _described(super().tools())


class Extensions(OpenAIExtensions):
    def tools(self) -> Sequence[ToolBinding]:
        return _described(super().tools())


settings = Settings(
    schema=TallySettings,
    read_tool="tally_settings_read",
    update_tool="tally_settings_update",
    layout=[
        OpenAISettingsGroup(title="Connection", items=[
            OpenAISettingsProperty(property="host"), OpenAISettingsProperty(property="port"),
            OpenAISettingsProperty(property="company"), OpenAISettingsProperty(property="timeout")]),
        OpenAISettingsGroup(title="Safety", items=[OpenAISettingsProperty(property="access")]),
        OpenAISettingsGroup(title="Advanced", items=[
            OpenAISettingsProperty(property="odbcDsn"), OpenAISettingsProperty(property="gstSchema")]),
    ],
)


@settings.read
def read_settings(context: Context[Any, Any]) -> TallySettings:
    return _settings_values()


@settings.update
def update_settings(set: dict[str, Any], context: Context[Any, Any]) -> TallySettings:  # noqa: A002
    try:
        config.update(set)
    except ValueError as exc:
        raise tx.TallyError(str(exc)) from exc
    return _settings_values()


# --------------------------------------------------------------------------
# OpenAI extension: composer @-mentions
# --------------------------------------------------------------------------

openai_extensions = Extensions()

_MENTION_TYPES = (("ledger", "Ledger"), ("group", "Group"), ("stock_item", "Stock item"))


@openai_extensions.mentions.search
def search_mentions(params: OpenAIMentionSearchParams, context: Context[Any, Any]) -> OpenAIMentionSearchResult:
    needle = params.query.strip().lower()
    items: list[ResourceLink] = []
    try:
        for kind, label in _MENTION_TYPES:
            for row in core.list_masters(kind):
                if needle in row["name"].lower():
                    parent = row.get("parent", "")
                    items.append(ResourceLink(
                        type="resource_link", uri=f"tally://{kind}/{quote(row['name'], safe='')}",
                        name=row["name"], title=row["name"],
                        description=f"{label}{' under ' + parent if parent else ''}",
                        mime_type="application/json"))
    except tx.TallyError:
        return OpenAIMentionSearchResult(items=[])
    items.sort(key=lambda link: (not link.name.lower().startswith(needle), link.name.lower()))
    return OpenAIMentionSearchResult(items=items[:25])


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------

def build_server() -> MCPServer:
    mcp = MCPServer(
        "rajtally",
        title="RAJ Tally FINL MCP",
        instructions=INSTRUCTIONS,
        version=__version__,
        extensions=[settings, openai_extensions],
        # lets clients on the older MCP handshake discover the settings capability too
        middleware=[settings.advertise_legacy_capability],
    )
    for module in (connection, masters, vouchers, reports, inventory, gst, compliance, advanced):
        module.register(mcp)

    @mcp.resource("tally://guide", name="Tally MCP guide", mime_type="text/plain",
                  description="Step-by-step map of every tool.")
    def guide() -> str:
        return connection.GUIDE

    @mcp.resource("tally://{master_type}/{name}", name="Tally master", mime_type="application/json",
                  description="One ledger, group or stock item, by type and exact name.")
    def master(master_type: str, name: str) -> str:
        spec = core.master_spec(master_type)
        wanted = unquote(name)
        elements = tx.collection(spec["type"], ["*"] + list(spec["fields"]),
                                 filters={"RajByName": f"$Name = {tx.tdl_string(wanted)}"})
        for element in elements:
            if (element.get("NAME") or tx.text_of(element, "NAME")).lower() == wanted.lower():
                return json.dumps({"summary": core.normalise_master(spec["key"], element),
                                   "all_fields": tx.to_dict(element)}, indent=2, ensure_ascii=False)
        raise tx.TallyError(f"No {spec['key']} named '{wanted}'.")

    return mcp
