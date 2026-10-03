"""Step 1 - connect: check Tally is reachable and pick the company to work on."""

from __future__ import annotations

from typing import Any

from .. import config, core
from .. import tally_xml as tx
from ..tally_xml import TallyError
from . import READ, WRITE

GUIDE = """RAJ Tally FINL MCP - how to use it

STEP 1  Connect      tally_status -> tally_list_companies -> tally_use_company -> tally_company_info
STEP 2  Look         tally_search, tally_list_masters, tally_get_master, tally_list_vouchers, tally_get_voucher
STEP 3  Report       tally_trial_balance, tally_balance_sheet, tally_profit_loss, tally_ledger_statement,
                     tally_outstandings, tally_cash_bank_balances, tally_group_summary, tally_register,
                     tally_monthly_summary, tally_stock_summary, tally_stock_movement, tally_native_report
                     (names: tally_list_native_reports)
STEP 4  Comply       tally_gst_summary, tally_gstr1, tally_hsn_summary, tally_validate_gstin, tally_tds_summary,
                     tally_bank_reconciliation, tally_audit_checks, tally_data_health
STEP 5  Write        tally_create_ledger, tally_create_group, tally_create_stock_item, tally_save_master,
                     tally_create_voucher, tally_create_invoice, tally_create_stock_journal,
                     tally_alter_voucher, tally_bulk_import, tally_import_xml
STEP 6  Remove       tally_rename_master, tally_delete_master, tally_cancel_voucher, tally_delete_voucher
ANYTHING ELSE        tally_sql (ODBC-style SELECT; help: tally_odbc_tables), tally_tdl_collection,
                     tally_evaluate, tally_raw_xml
SETTINGS             tally_settings_read, tally_settings_update (host, port, company, access, Tally version)

Rules of thumb
- Dates: YYYY-MM-DD. Leave both dates blank for the current financial year, or pass 'FY2024-25'.
- Amounts: plain positive numbers; say dr or cr. Reports show dr / cr columns.
- Every write tool takes dry_run=true: it returns the XML it would send and changes nothing.
- Access levels (tally_settings_update): read_only | read_write | full. Delete and cancel need 'full'
  and confirm=true.
- Names must match Tally exactly. Use tally_search first when unsure.
"""


def active_company() -> str:
    """The company Tally will use when no company is named."""
    cfg = config.get()
    if cfg.company:
        return cfg.company
    rows = _companies()
    for row in rows:
        if row.get("active"):
            return row["name"]
    return rows[0]["name"] if rows else ""


def _companies() -> list[dict[str, Any]]:
    elements = tx.collection(
        "Company", core.MASTER_TYPES["company"]["fields"],
        compute={"RajIsActive": "$Name = ##SVCurrentCompany"}, company="",
    )
    rows = []
    keep = ("name", "financial_year_from", "books_from", "ending_at", "company_number", "guid")
    for element in elements:
        full = core.normalise_master("company", element)
        row = {key: full[key] for key in keep if full.get(key)}
        row["active"] = tx.yes(tx.text_of(element, "RAJISACTIVE"))
        rows.append(row)
    return rows


def register(mcp: Any) -> None:

    @mcp.tool(annotations=READ)
    def tally_guide() -> str:
        """START HERE. The step-by-step map of every tool in this server and the rules for using them."""
        return GUIDE

    @mcp.tool(annotations=READ)
    def tally_status() -> dict[str, Any]:
        """Check the connection to Tally: is it running, which product, which companies are open,
        which company is selected, and whether writing is allowed. Run this first if anything fails."""
        cfg = config.get()
        status: dict[str, Any] = {"url": cfg.url, "access": cfg.access, "selected_company": cfg.company or None}
        try:
            banner = tx.ping()
        except TallyError as exc:
            status.update(connected=False, problem=str(exc))
            return status
        status.update(connected=True, tally_says=banner,
                      product="TallyPrime" if "prime" in banner.lower() else "Tally.ERP 9"
                      if "erp" in banner.lower() else "Tally")
        try:
            companies = _companies()
            status["open_companies"] = [c["name"] for c in companies]
            status["active_company"] = next((c["name"] for c in companies if c.get("active")), None)
            if not companies:
                status["problem"] = "Tally is running but no company is open. Open a company in Tally."
            elif cfg.company and cfg.company not in status["open_companies"]:
                status["problem"] = (f"The selected company '{cfg.company}' is not open in Tally. "
                                     "Open it, or pick another with tally_use_company.")
        except TallyError as exc:
            status["problem"] = str(exc)
        for label, param in (("serial_number", "SerialNumber"), ("educational_mode", "IsEducationalMode")):
            try:
                value = tx.evaluate("$$LicenseInfo", [param], company="")
                if value:
                    status[label] = value
            except TallyError:
                pass
        from .advanced import odbc_state
        status["odbc"] = odbc_state()
        return status

    @mcp.tool(annotations=READ)
    def tally_list_companies() -> dict[str, Any]:
        """List the companies currently open in Tally, with their financial-year and books-from dates."""
        rows = _companies()
        return {"selected_company": config.get().company or None, "total": len(rows), "companies": rows}

    @mcp.tool(annotations=WRITE)
    def tally_use_company(name: str, remember: bool = True) -> dict[str, Any]:
        """Choose the company every other tool works on. Pass the exact name from tally_list_companies,
        or '' to follow whichever company is active in Tally. remember=true saves the choice."""
        name = name.strip()
        if name:
            names = [c["name"] for c in _companies()]
            exact = [n for n in names if n.lower() == name.lower()]
            if not exact:
                raise TallyError(f"'{name}' is not open in Tally. Open companies: {', '.join(names) or 'none'}.")
            name = exact[0]
        config.update({"company": name}, persist=remember)
        return {"selected_company": name or None,
                "note": "All tools now use this company." if name else "Following Tally's active company."}

    @mcp.tool(annotations=READ)
    def tally_company_info(company: str | None = None, all_fields: bool = False) -> dict[str, Any]:
        """Company profile: name, address, state, GSTIN, PAN, financial year, and which features
        (inventory, bill-wise, cost centres, GST, TDS, payroll) are switched on.
        all_fields=true returns every stored field."""
        target = company or active_company()
        if not target:
            raise TallyError("No company is open in Tally.")
        elements = [e for e in tx.collection("Company", ["*"], company=target)
                    if (e.get("NAME") or tx.text_of(e, "NAME")).lower() == target.lower()]
        if not elements:
            raise TallyError(f"Company '{target}' is not open in Tally.")
        element = elements[0]
        if all_fields:
            return tx.to_dict(element)
        info: dict[str, Any] = {"name": element.get("NAME") or tx.text_of(element, "NAME")}
        simple = {
            "BASICCOMPANYFORMALNAME": "formal_name", "STATENAME": "state", "COUNTRYNAME": "country",
            "PINCODE": "pincode", "PHONENUMBER": "phone", "MOBILENUMBERS": "mobile", "EMAIL": "email",
            "WEBSITE": "website", "INCOMETAXNUMBER": "pan", "GSTREGISTRATIONNUMBER": "gstin",
            "GSTIN": "gstin", "TANUMBER": "tan", "COMPANYNUMBER": "company_number", "GUID": "guid",
        }
        for tag, key in simple.items():
            for found in element.iter(tag):
                if found.text and found.text.strip() and len(found) == 0:
                    info[key] = found.text.strip()
        address = [a.text.strip() for a in element.iter("ADDRESS") if a.text and a.text.strip()]
        if address:
            info["address"] = address
        for tag, key in (("STARTINGFROM", "financial_year_from"), ("BOOKSFROM", "books_from"),
                         ("ENDINGAT", "ending_at")):
            value = tx.text_of(element, tag)
            if value:
                info[key] = tx.iso_date(value)
        features = {
            "ISINVENTORYON": "inventory", "ISINTEGRATED": "accounts_inventory_integrated",
            "ISBILLWISEON": "bill_wise", "ISCOSTCENTRESON": "cost_centres", "ISGSTON": "gst",
            "ISTDSON": "tds", "ISTCSON": "tcs", "ISPAYROLLON": "payroll", "ISINTERESTON": "interest",
            "USEFORGODOWNS": "multiple_godowns", "ISBATCHWISEON": "batches",
        }
        info["features"] = {key: tx.yes(tx.text_of(element, tag)) for tag, key in features.items()
                            if element.find(tag) is not None}
        return info
