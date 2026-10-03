"""Anything the other tools do not cover: SQL over ODBC, custom TDL collections, TDL functions, raw XML."""

from __future__ import annotations

import re
from typing import Any

from .. import config, core
from .. import tally_xml as tx
from ..tally_xml import TallyError
from . import READ, WRITE

# ODBC table name -> Tally object type, for running the same SELECT over XML
TABLES = {
    "ledger": "Ledger", "ledgers": "Ledger", "group": "Group", "groups": "Group",
    "stockitem": "StockItem", "stockitems": "StockItem", "stockgroup": "StockGroup",
    "stockgroups": "StockGroup", "stockcategory": "StockCategory", "stockcategories": "StockCategory",
    "unit": "Unit", "units": "Unit", "godown": "Godown", "godowns": "Godown",
    "costcentre": "CostCentre", "costcentres": "CostCentre", "costcenter": "CostCentre",
    "costcategory": "CostCategory", "costcategories": "CostCategory", "vouchertype": "VoucherType",
    "vouchertypes": "VoucherType", "currency": "Currency", "currencies": "Currency",
    "company": "Company", "companies": "Company", "voucher": "Voucher", "vouchers": "Voucher",
    "bills": "Bills", "bill": "Bills", "budget": "Budget", "budgets": "Budget",
    "attendancetype": "AttendanceType", "attendancetypes": "AttendanceType",
}

ODBC_HELP = {
    "tables": ["Ledger", "Group", "StockItem", "StockGroup", "StockCategory", "Unit", "Godown", "CostCentre",
               "CostCategory", "VoucherType", "Currency", "Company", "Voucher", "Bills", "Budget"],
    "common_fields": {
        "Ledger": ["$Name", "$Parent", "$OpeningBalance", "$ClosingBalance", "$PartyGSTIN", "$LedStateName",
                   "$IncomeTaxNumber", "$Email", "$LedgerMobile", "$IsBillWiseOn", "$MasterId", "$GUID"],
        "Group": ["$Name", "$Parent", "$IsRevenue", "$IsDeemedPositive", "$ClosingBalance"],
        "StockItem": ["$Name", "$Parent", "$Category", "$BaseUnits", "$OpeningBalance", "$OpeningValue",
                      "$ClosingBalance", "$ClosingRate", "$ClosingValue"],
        "Voucher": ["$Date", "$VoucherTypeName", "$VoucherNumber", "$PartyLedgerName", "$Amount",
                    "$Narration", "$MasterId", "$GUID"],
        "Company": ["$Name", "$StartingFrom", "$BooksFrom"],
    },
    "examples": [
        "SELECT $Name, $Parent, $ClosingBalance FROM Ledger",
        "SELECT $Name, $ClosingBalance FROM Ledger WHERE $Parent = 'Sundry Debtors'",
        "SELECT $Name, $ClosingBalance, $ClosingValue FROM StockItem",
        "SELECT $Name FROM Ledger WHERE $Name LIKE 'A%'",
    ],
    "notes": [
        "Tally's ODBC interface is read-only: it answers SELECT only. All writing goes through the XML "
        "tools (tally_create_*, tally_save_master, tally_import_xml).",
        "Field names start with $ and are Tally's method names.",
        "A custom TDL collection can be used as a table only if its TDL marks it 'IsODBCTable: Yes'.",
    ],
}

_SELECT = re.compile(r"^\s*select\s+(?P<cols>.+?)\s+from\s+(?P<table>[\w.\[\]]+)"
                     r"(?:\s+where\s+(?P<where>.+?))?(?:\s+order\s+by\s+(?P<order>.+?))?\s*;?\s*$",
                     re.IGNORECASE | re.DOTALL)


def odbc_state() -> dict[str, Any]:
    """Is ODBC usable from this machine?"""
    try:
        import pyodbc
    except ImportError:
        return {"usable": False, "reason": "Python package 'pyodbc' is not installed. tally_sql still works "
                                           "- it runs the same query over XML. To use real ODBC: "
                                           "pip install pyodbc (Windows, with Tally's ODBC driver)."}
    drivers = [d for d in pyodbc.drivers() if "tally" in d.lower()]
    sources = [s for s in pyodbc.dataSources() if "tally" in s.lower()]
    usable = bool(drivers or sources or config.get().odbc_dsn)
    state: dict[str, Any] = {"usable": usable, "tally_drivers": drivers, "tally_data_sources": sources}
    if not usable:
        state["reason"] = ("No Tally ODBC driver found. In Tally: F1 Help > Settings > Connectivity > "
                           "'Enable ODBC' = Yes, then restart Tally. tally_sql works over XML meanwhile.")
    return state


def _odbc_connection_string() -> str:
    import pyodbc
    cfg = config.get()
    if cfg.odbc_dsn:
        return cfg.odbc_dsn if "=" in cfg.odbc_dsn else f"DSN={cfg.odbc_dsn}"
    sources = [s for s in pyodbc.dataSources() if "tally" in s.lower()]
    for source in sources:
        if source.endswith(str(cfg.port)):
            return f"DSN={source}"
    if sources:
        return f"DSN={sources[0]}"
    drivers = [d for d in pyodbc.drivers() if "tally" in d.lower()]
    if drivers:
        return f"DRIVER={{{drivers[0]}}};SERVER={cfg.host};PORT={cfg.port}"
    raise TallyError("No Tally ODBC driver or data source found on this machine.")


def run_odbc(sql: str, limit: int) -> dict[str, Any]:
    import pyodbc
    try:
        connection = pyodbc.connect(_odbc_connection_string(), timeout=config.get().timeout, autocommit=True)
    except pyodbc.Error as exc:
        raise TallyError(f"ODBC connection to Tally failed: {exc}") from exc
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, [None if v is None else v if isinstance(v, (int, float, str, bool))
                                   else str(v) for v in record])) for record in cursor.fetchmany(limit + 1)]
    except pyodbc.Error as exc:
        raise TallyError(f"Tally ODBC rejected the query: {exc}") from exc
    finally:
        connection.close()
    more = len(rows) > limit
    return {"via": "odbc", "columns": columns, "returned": min(len(rows), limit), "rows": rows[:limit],
            **({"more": "More rows exist; raise limit."} if more else {})}


def _where_to_tdl(where: str) -> str:
    """SQL condition -> TDL formula (quotes, LIKE, <>)."""

    def like(match: re.Match[str]) -> str:
        field, pattern = match.group(1), match.group(2)
        core_text = pattern.strip("%")
        if pattern.startswith("%") and pattern.endswith("%"):
            return f'{field} CONTAINS "{core_text}"'
        if pattern.endswith("%"):
            return f'{field} STARTING WITH "{core_text}"'
        if pattern.startswith("%"):
            return f'{field} ENDING WITH "{core_text}"'
        return f'{field} = "{core_text}"'

    formula = re.sub(r"(\$[\w.]+)\s+like\s+'([^']*)'", like, where, flags=re.IGNORECASE)
    formula = re.sub(r"'([^']*)'", r'"\1"', formula)
    return formula.replace("!=", "<>")


def run_sql_over_xml(sql: str, limit: int, company: str | None, from_date: Any, to_date: Any
                     ) -> dict[str, Any]:
    match = _SELECT.match(sql)
    if not match:
        raise TallyError("Could not read that query. Supported form: SELECT $Field, $Field FROM Table "
                         "[WHERE condition] [ORDER BY $Field]. See tally_odbc_tables for examples.")
    table = match["table"].strip("[]")
    object_type = TABLES.get(table.lower(), table)
    raw_columns = [c.strip() for c in match["cols"].split(",") if c.strip()]
    star = raw_columns == ["*"]
    fields = ["*"] if star else [c.lstrip("$") for c in raw_columns]
    filters = {"RajWhere": _where_to_tdl(match["where"])} if match["where"] else None
    elements = tx.collection(object_type, fields, filters=filters, company=company, from_date=from_date,
                             to_date=to_date)
    rows = []
    for element in elements:
        if star:
            data = tx.to_dict(element)
            rows.append(data if isinstance(data, dict) else {"value": data})
            continue
        row = {}
        for column, field in zip(raw_columns, fields):
            tag = field.upper()
            if tag == "NAME":
                row[column] = element.get("NAME") or tx.text_of(element, "NAME")
            else:
                found = element.find(tag) if "." not in tag else None
                row[column] = (found.text or "").strip() if found is not None and len(found) == 0 else (
                    tx.to_dict(found) if found is not None else "")
        rows.append(row)
    if match["order"]:
        key = match["order"].split(",")[0].strip()
        descending = key.lower().endswith(" desc")
        key = re.sub(r"\s+(asc|desc)$", "", key, flags=re.IGNORECASE)
        rows.sort(key=lambda r: str(r.get(key, "")).lower(), reverse=descending)
    return {"via": "xml", "object_type": object_type, **core.page(rows, limit, 0, "rows")}


def register(mcp: Any) -> None:

    @mcp.tool(annotations=READ)
    def tally_sql(sql: str, via: str = "auto", limit: int = 500, from_date: str | None = None,
                  to_date: str | None = None, company: str | None = None) -> dict[str, Any]:
        """Run an ODBC-style SELECT against Tally, e.g.
        SELECT $Name, $Parent, $ClosingBalance FROM Ledger WHERE $Parent = 'Sundry Debtors'

        via: 'odbc' uses Tally's ODBC driver (Windows, driver installed); 'xml' runs the same query over
        Tally's XML port (works everywhere); 'auto' tries ODBC and falls back to XML.
        Read-only: only SELECT is accepted. Call tally_odbc_tables for table and field names."""
        if not re.match(r"^\s*select\b", sql, re.IGNORECASE) or ";" in sql.strip().rstrip(";"):
            raise TallyError("Only a single SELECT statement is allowed. Tally's ODBC interface is read-only; "
                             "use the write tools to change data.")
        mode = via.strip().lower()
        if mode not in ("auto", "odbc", "xml"):
            raise TallyError("via must be 'auto', 'odbc' or 'xml'.")
        limit = max(1, min(int(limit), 5000))
        if mode in ("auto", "odbc"):
            state = odbc_state()
            if state["usable"]:
                try:
                    return run_odbc(sql, limit)
                except TallyError as exc:
                    if mode == "odbc":
                        raise
                    fallback = str(exc)
                else:
                    fallback = ""
            elif mode == "odbc":
                raise TallyError(state["reason"])
            else:
                fallback = state["reason"]
        else:
            fallback = ""
        start = tx.parse_date(from_date) if from_date else None
        end = tx.parse_date(to_date) if to_date else None
        result = run_sql_over_xml(sql, limit, company, start, end)
        if fallback and mode == "auto":
            result["odbc_not_used_because"] = fallback
        return result

    @mcp.tool(annotations=READ)
    def tally_odbc_tables() -> dict[str, Any]:
        """Table names, common field names and example queries for tally_sql, and whether the Tally ODBC
        driver is usable on this machine."""
        return {**ODBC_HELP, "odbc": odbc_state()}

    @mcp.tool(annotations=READ)
    def tally_tdl_collection(object_type: str, fields: list[str], filters: dict[str, str] | None = None,
                             child_of: str | None = None, compute: dict[str, str] | None = None,
                             from_date: str | None = None, to_date: str | None = None, limit: int = 500,
                             raw: bool = False, company: str | None = None) -> dict[str, Any]:
        """Build and run a custom TDL collection - the most flexible way to read Tally.

        object_type: any Tally object type, e.g. Ledger, Group, Voucher, StockItem, Bills, CostCentre,
        Godown, VoucherType, Company. fields: method names to fetch, e.g. ["Name", "Parent",
        "ClosingBalance"]; use "*" for every stored field and "AllLedgerEntries.*" for sub-lists.
        filters: name -> TDL formula, all must be true, e.g.
          {"BigDebtors": "$ClosingBalance < -100000"}   (debit balances are negative in Tally)
          {"OnlySales": "$$IsSales:$VoucherTypeName"}
        compute: extra calculated fields, name -> TDL formula. child_of: only objects under this parent.
        raw=true returns the XML."""
        xml = tx.collection_xml(object_type, fields, filters=filters, child_of=child_of, compute=compute,
                                company=company, from_date=from_date or None, to_date=to_date or None)
        if raw:
            text = tx.post(xml)
            tx.parse(text)
            return {"request": xml, "xml": text[:200000], "truncated": len(text) > 200000}
        elements = tx.collection(object_type, fields, filters=filters, child_of=child_of, compute=compute,
                                 company=company, from_date=from_date or None, to_date=to_date or None)
        rows = []
        for element in elements:
            data = tx.to_dict(element)
            rows.append(data if isinstance(data, dict) else {"NAME": element.get("NAME", ""), "value": data})
        return core.page(rows, limit, 0, "rows")

    @mcp.tool(annotations=READ)
    def tally_evaluate(function: str, params: list[str] | None = None, company: str | None = None
                       ) -> dict[str, Any]:
        """Evaluate a TDL function inside Tally and return its result.
        Examples: function='$$LicenseInfo', params=['SerialNumber'] ;
        function='$$LicenseInfo', params=['IsEducationalMode']."""
        return {"function": function, "params": params or [],
                "result": tx.evaluate(function, params or [], company)}

    @mcp.tool(annotations=WRITE)
    def tally_raw_xml(xml: str, confirm_write: bool = False) -> dict[str, Any]:
        """Send a complete Tally XML request (<ENVELOPE>...</ENVELOPE>) exactly as written and return
        Tally's reply. The escape hatch for anything else Tally's XML interface supports.
        Export requests need no confirmation. Import requests change data, so they need
        confirm_write=true and write access; deletes and cancels need access = 'full'."""
        text = xml.strip()
        if not text.upper().startswith("<ENVELOPE"):
            raise TallyError("The request must be a complete <ENVELOPE>...</ENVELOPE>.")
        is_import = core.is_import(text)
        removes = core.is_destructive(text)
        if is_import or removes:
            core.require_write()
            if removes:
                core.require_full(confirm_write)
            elif not confirm_write:
                raise TallyError("This request imports data into Tally. Call again with confirm_write=true.")
        reply = tx.post(text)
        result: dict[str, Any] = {"response": reply[:200000], "truncated": len(reply) > 200000}
        if is_import:
            try:
                result["import_result"] = tx.import_result(reply)
            except TallyError as exc:
                result["import_result"] = {"ok": False, "problem": str(exc)}
        return result
