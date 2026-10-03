"""Regenerate docs/TOOLS.md from the live tool list:  python scripts/make_tool_docs.py"""

import asyncio
from pathlib import Path

from mcp.client import Client

from rajtally_mcp.server import build_server

SECTIONS = [
    ("Step 1 - Connect", ["tally_guide", "tally_status", "tally_list_companies", "tally_use_company",
                          "tally_company_info", "tally_settings_read", "tally_settings_update"]),
    ("Step 2 - Look things up", ["tally_search", "tally_list_masters", "tally_get_master",
                                 "tally_list_vouchers", "tally_get_voucher"]),
    ("Step 3 - Reports", ["tally_trial_balance", "tally_balance_sheet", "tally_profit_loss",
                          "tally_ledger_statement", "tally_outstandings", "tally_cash_bank_balances",
                          "tally_group_summary", "tally_monthly_summary", "tally_register",
                          "tally_stock_summary", "tally_stock_movement", "tally_native_report",
                          "tally_list_native_reports"]),
    ("Step 4 - GST, TDS, bank and audit", ["tally_gst_summary", "tally_gstr1", "tally_hsn_summary",
                                           "tally_validate_gstin", "tally_tds_summary",
                                           "tally_bank_reconciliation", "tally_audit_checks",
                                           "tally_data_health"]),
    ("Step 5 - Create and edit", ["tally_create_ledger", "tally_create_group", "tally_create_stock_item",
                                  "tally_save_master", "tally_rename_master", "tally_create_voucher",
                                  "tally_create_invoice", "tally_create_stock_journal", "tally_alter_voucher",
                                  "tally_bulk_import", "tally_import_xml"]),
    ("Step 6 - Remove (needs access = full)", ["tally_delete_master", "tally_cancel_voucher",
                                               "tally_delete_voucher"]),
    ("Anything else", ["tally_sql", "tally_odbc_tables", "tally_tdl_collection", "tally_evaluate",
                       "tally_raw_xml", "search_mentions"]),
]


async def main() -> None:
    async with Client(build_server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    listed = [name for _, names in SECTIONS for name in names]
    assert sorted(listed) == sorted(tools), set(tools) ^ set(listed)
    lines = ["# Tool reference", "",
             f"All {len(tools)} tools, in the order you would use them. Generated from the server itself "
             "by `scripts/make_tool_docs.py`.", "",
             "Every tool also accepts `company` to work on a company other than the selected one, and every "
             "write tool accepts `dry_run` to preview without changing anything.", ""]
    for title, names in SECTIONS:
        lines += [f"## {title}", ""]
        for name in names:
            tool = tools[name]
            if name in ("tally_use_company", "tally_settings_update"):
                kind = "settings"
            elif tool.annotations and tool.annotations.read_only_hint:
                kind = "read"
            elif tool.annotations and tool.annotations.destructive_hint:
                kind = "delete"
            else:
                kind = "write"
            lines += [f"### `{name}`  ({kind})", "", " ".join((tool.description or "").split("\n\n")[0].split()), ""]
            rest = (tool.description or "").split("\n\n", 1)
            if len(rest) > 1:
                lines += ["```", *[line.strip() for line in rest[1].strip().splitlines()], "```", ""]
            properties = tool.input_schema.get("properties", {})
            required = set(tool.input_schema.get("required", []))
            params = [f"`{key}`{'' if key in required else '?'}" for key in properties
                      if key not in ("company", "dry_run")]
            if params:
                lines += ["Arguments: " + ", ".join(params) + "  (`?` = optional)", ""]
    Path(__file__).resolve().parents[1].joinpath("docs", "TOOLS.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"docs/TOOLS.md written: {len(tools)} tools")


asyncio.run(main())
