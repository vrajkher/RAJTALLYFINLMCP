"""Masters: ledgers, groups, stock items, units, godowns, cost centres, voucher types, employees ...

Read with tally_search / tally_list_masters / tally_get_master.
Write with tally_create_ledger / tally_create_group / tally_create_stock_item, or tally_save_master
for any other type and for editing existing masters.
"""

from __future__ import annotations

from typing import Any

from .. import builders, core
from .. import tally_xml as tx
from ..tally_xml import TallyError
from . import DELETE, READ, WRITE

MASTER_TYPE_HELP = ("ledger, group, stock_item, stock_group, stock_category, unit, godown, cost_centre, "
                    "cost_category, voucher_type, currency, budget, employee, employee_group, pay_head, "
                    "attendance_type")


def send_master(xml: str, dry_run: bool, company: str | None) -> dict[str, Any]:
    """Post master XML (or just show it when dry_run)."""
    core.require_write()
    if dry_run:
        return {"dry_run": True, "nothing_changed": True, "xml": xml}
    return tx.import_data(xml, "All Masters", company)


def register(mcp: Any) -> None:

    @mcp.tool(annotations=READ)
    def tally_search(text: str, types: list[str] | None = None, limit: int = 25,
                     company: str | None = None) -> dict[str, Any]:
        """Find masters by part of their name. Use this before any tool that needs an exact Tally name.
        types defaults to ledger, group and stock_item; any master type is allowed."""
        needle = text.strip().lower()
        if not needle:
            raise TallyError("Give some text to search for.")
        found: list[dict[str, Any]] = []
        start, end = tx.period(None, None)
        for kind in types or ["ledger", "group", "stock_item"]:
            for row in core.list_masters(kind, company=company, from_date=start, to_date=end):
                if needle in row["name"].lower():
                    hit = {"type": core.master_spec(kind)["key"], "name": row["name"]}
                    for key in ("parent", "gstin", "closing_balance", "closing_balance_type", "closing_qty",
                                "unit"):
                        if row.get(key) not in (None, ""):
                            hit[key] = row[key]
                    found.append(hit)
        found.sort(key=lambda r: (not r["name"].lower().startswith(needle), r["name"].lower()))
        return core.page(found, limit, 0, "matches")

    @mcp.tool(annotations=READ)
    def tally_list_masters(master_type: str, parent: str | None = None, search: str | None = None,
                           from_date: str | None = None, to_date: str | None = None,
                           limit: int = 200, offset: int = 0, company: str | None = None) -> dict[str, Any]:
        """List masters of one type with their key fields (balances for ledgers/groups, quantity and
        value for stock items).

        master_type: ledger, group, stock_item, stock_group, stock_category, unit, godown, cost_centre,
        cost_category, voucher_type, currency, budget, employee, employee_group, pay_head, attendance_type.
        parent: only masters under this group (e.g. 'Sundry Debtors'), including sub-groups.
        search: only names containing this text.
        from_date / to_date: the period for opening and closing balances (blank = current financial year)."""
        start, end = tx.period(from_date, to_date)
        rows = core.list_masters(master_type, parent=parent, search=search, company=company,
                                 from_date=start, to_date=end)
        return core.page(rows, limit, offset, "masters")

    @mcp.tool(annotations=READ)
    def tally_get_master(master_type: str, name: str, company: str | None = None) -> dict[str, Any]:
        """Everything Tally stores about one master (all fields, exactly as Tally names them), plus a
        short readable summary. Use the exact name."""
        spec = core.master_spec(master_type)
        elements = tx.collection(spec["type"], ["*"] + list(spec["fields"]),
                                 filters={"RajByName": f"$Name = {tx.tdl_string(name)}"}, company=company)
        match = [e for e in elements if (e.get("NAME") or tx.text_of(e, "NAME")).lower() == name.strip().lower()]
        if not match:
            raise TallyError(f"No {spec['key']} named '{name}'. Try tally_search to find the exact name.")
        return {"summary": core.normalise_master(spec["key"], match[0]), "all_fields": tx.to_dict(match[0])}

    @mcp.tool(annotations=WRITE)
    def tally_create_ledger(
        name: str,
        group: str,
        opening_balance: float = 0,
        opening_type: str = "Dr",
        gstin: str | None = None,
        gst_registration_type: str | None = None,
        state: str | None = None,
        country: str | None = None,
        address: list[str] | None = None,
        pincode: str | None = None,
        pan: str | None = None,
        email: str | None = None,
        mobile: str | None = None,
        mailing_name: str | None = None,
        bill_wise: bool | None = None,
        credit_period_days: int | None = None,
        tax_type: str | None = None,
        gst_duty_head: str | None = None,
        alias: str | None = None,
        fields: dict[str, Any] | None = None,
        dry_run: bool = False,
        company: str | None = None,
    ) -> dict[str, Any]:
        """Create a ledger (customer, supplier, bank, expense, income, tax ...).

        group: the Tally group it sits under, e.g. 'Sundry Debtors', 'Sundry Creditors', 'Bank Accounts',
        'Indirect Expenses', 'Sales Accounts', 'Duties & Taxes'.
        opening_type: 'Dr' or 'Cr'. gst_registration_type: Regular, Composition, Unregistered, Consumer.
        For tax ledgers: tax_type 'GST' / 'TDS' and gst_duty_head 'CGST', 'SGST/UTGST', 'IGST'
        (Tally.ERP 9: 'Central Tax', 'State Tax', 'Integrated Tax').
        fields: any other Tally tags, e.g. {"ISCOSTCENTRESON": "Yes"}."""
        if gstin:
            from .gst import check_gstin
            verdict = check_gstin(gstin)
            if not verdict["valid"]:
                raise TallyError(f"GSTIN {gstin} looks wrong: {verdict['problem']}")
        body = builders.ledger_body(
            parent=group, opening_balance=opening_balance, opening_type=opening_type, gstin=gstin,
            gst_registration_type=gst_registration_type, state=state, country=country, address=address,
            pincode=pincode, pan=pan, email=email, mobile=mobile,
            mailing_name=mailing_name or (name if (address or state or gstin) else None),
            bill_wise=bill_wise, credit_period_days=credit_period_days, tax_type=tax_type,
            gst_duty_head=gst_duty_head, applicable_from=core.books_from(company), fields=fields)
        return send_master(builders.master_xml("LEDGER", name, body, alias=alias), dry_run, company)

    @mcp.tool(annotations=WRITE)
    def tally_create_group(name: str, parent: str, fields: dict[str, Any] | None = None,
                           dry_run: bool = False, company: str | None = None) -> dict[str, Any]:
        """Create an accounting group under an existing group (e.g. 'North Zone Debtors' under
        'Sundry Debtors'). Use parent='Primary' only for a new top-level group, and then also pass
        fields such as {"ISREVENUE": "No", "ISDEEMEDPOSITIVE": "Yes"}."""
        body = builders.el("PARENT", "" if parent.strip().lower() == "primary" else parent)
        body += builders.extra_xml(fields)
        return send_master(builders.master_xml("GROUP", name, body), dry_run, company)

    @mcp.tool(annotations=WRITE)
    def tally_create_stock_item(
        name: str,
        unit: str,
        stock_group: str | None = None,
        category: str | None = None,
        hsn: str | None = None,
        gst_rate: float | None = None,
        taxability: str | None = None,
        opening_qty: float = 0,
        opening_rate: float = 0,
        opening_value: float = 0,
        godown: str = "Main Location",
        description: str | None = None,
        alias: str | None = None,
        fields: dict[str, Any] | None = None,
        dry_run: bool = False,
        company: str | None = None,
    ) -> dict[str, Any]:
        """Create a stock item. unit must already exist (see tally_list_masters unit; create one with
        tally_save_master). gst_rate is the total GST % (e.g. 18); taxability: Taxable, Exempt, Nil Rated.
        Opening stock: give opening_qty with opening_rate or opening_value."""
        body = builders.stock_item_body(
            parent=stock_group, category=category, unit=unit, opening_qty=opening_qty,
            opening_rate=opening_rate, opening_value=opening_value, godown=godown, hsn=hsn,
            gst_rate=gst_rate, taxability=taxability, description=description,
            applicable_from=core.books_from(company), fields=fields)
        return send_master(builders.master_xml("STOCKITEM", name, body, alias=alias), dry_run, company)

    @mcp.tool(annotations=WRITE)
    def tally_save_master(master_type: str, name: str, fields: dict[str, Any] | None = None,
                          action: str = "create", dry_run: bool = False,
                          company: str | None = None) -> dict[str, Any]:
        """Create or edit ANY master using Tally's own field names. This is the general-purpose writer.

        action: 'create' or 'alter' (alter changes only the fields you pass).
        fields: Tally tags and values. Nested lists use a '.LIST' key. Examples:
          unit          {"ISSIMPLEUNIT": "Yes", "ORIGINALNAME": "Numbers", "DECIMALPLACES": 0}
          godown        {"PARENT": ""}
          cost_centre   {"PARENT": "", "CATEGORY": "Primary Cost Category"}
          stock_group   {"PARENT": ""}
          voucher_type  {"PARENT": "Sales", "NUMBERINGMETHOD": "Automatic"}
          ledger alter  {"PARENT": "Sundry Creditors", "EMAIL": "a@b.com"}
          employee      {"PARENT": "Staff", "DATEOFJOIN": "20240401", "DESIGNATION": "Clerk"}
        Read an existing master with tally_get_master to see the exact tags it uses."""
        spec = core.master_spec(master_type)
        if spec["key"] == "company":
            raise TallyError("Companies cannot be created or edited through this server.")
        verb = action.strip().lower()
        if verb not in ("create", "alter"):
            raise TallyError("action must be 'create' or 'alter'.")
        merged = dict(spec.get("extra") or {}) if verb == "create" else {}
        merged.update(fields or {})
        body = builders.extra_xml(merged)
        return send_master(builders.master_xml(spec["tag"], name, body, action=verb.capitalize()), dry_run,
                           company)

    @mcp.tool(annotations=WRITE)
    def tally_rename_master(master_type: str, name: str, new_name: str, dry_run: bool = False,
                            company: str | None = None) -> dict[str, Any]:
        """Rename a master. Existing vouchers follow the new name automatically."""
        spec = core.master_spec(master_type)
        if spec["key"] == "company":
            raise TallyError("Companies cannot be renamed through this server.")
        xml = builders.master_xml(spec["tag"], name, "", action="Alter", new_name=new_name)
        return send_master(xml, dry_run, company)

    @mcp.tool(annotations=DELETE)
    def tally_delete_master(master_type: str, name: str, confirm: bool = False, dry_run: bool = False,
                            company: str | None = None) -> dict[str, Any]:
        """Delete a master. Tally refuses if it is used in any voucher or has children.
        Needs access = 'full' and confirm=true."""
        spec = core.master_spec(master_type)
        if spec["key"] == "company":
            raise TallyError("Companies cannot be deleted through this server.")
        xml = builders.delete_master_xml(spec["tag"], name)
        if dry_run:
            return {"dry_run": True, "nothing_changed": True, "xml": xml}
        core.require_full(confirm)
        result = tx.import_data(xml, "All Masters", company)
        if not result.get("deleted"):
            result["ok"] = False
            result.setdefault("note", "Nothing was deleted. Tally keeps masters that are in use.")
        return result
