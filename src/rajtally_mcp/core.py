"""Shared building blocks used by every tool module.

* access checks (read_only / read_write / full)
* the registry of master types (ledger, group, stock item ...)
* reading masters and vouchers into plain dictionaries
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Any

from . import config
from . import tally_xml as tx
from .tally_xml import TallyError

# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------


def require_write() -> None:
    if config.get().access == "read_only":
        raise TallyError(
            "This server is in read_only mode, so nothing was changed in Tally. "
            "To allow changes, set access to 'read_write' with tally_settings_update."
        )


def require_full(confirm: bool) -> None:
    if config.get().access != "full":
        raise TallyError(
            "Deleting and cancelling need access = 'full' (current: "
            f"'{config.get().access}'). Change it with tally_settings_update, then try again."
        )
    if not confirm:
        raise TallyError("This permanently changes Tally data. Call again with confirm=true to go ahead.")


_books_from: dict[tuple[str, str], str] = {}


def books_from(company: str | None = None) -> str:
    """The date the company's books begin (YYYYMMDD) - the 'applicable from' date for GST and
    address details on new masters. Falls back to the start of the current financial year."""
    key = (config.get().url, company if company is not None else config.get().company)
    if key not in _books_from:
        found = ""
        try:
            for element in tx.collection("Company", ["Name", "BooksFrom", "StartingFrom"],
                                         compute={"RajIsActive": "$Name = ##SVCurrentCompany"}, company=""):
                name = element.get("NAME") or tx.text_of(element, "NAME")
                if (key[1] and name.lower() == key[1].lower()) or (
                        not key[1] and tx.yes(tx.text_of(element, "RAJISACTIVE"))):
                    found = tx.text_of(element, "BOOKSFROM") or tx.text_of(element, "STARTINGFROM")
        except TallyError:
            pass
        _books_from[key] = tx.tally_date(found) if found else tx.tally_date(tx.financial_year()[0])
    return _books_from[key]


def page(rows: list[Any], limit: int = 200, offset: int = 0, label: str = "rows") -> dict[str, Any]:
    """Return one page of a list plus the counts an assistant needs to know whether more exist."""
    limit = max(1, min(int(limit or 200), 5000))
    offset = max(0, int(offset or 0))
    chunk = rows[offset:offset + limit]
    result: dict[str, Any] = {"total": len(rows), "returned": len(chunk), label: chunk}
    if offset + len(chunk) < len(rows):
        result["more"] = f"{len(rows) - offset - len(chunk)} more. Call again with offset={offset + len(chunk)}."
    return result


# --------------------------------------------------------------------------
# Master types
# --------------------------------------------------------------------------
# key -> Tally collection type, XML tag, fields to fetch, optional filter

MASTER_TYPES: dict[str, dict[str, Any]] = {
    "ledger": {
        "type": "Ledger", "tag": "LEDGER",
        "fields": ["Name", "Parent", "OpeningBalance", "ClosingBalance", "PartyGSTIN", "GSTRegistrationType",
                   "LedStateName", "CountryName", "IncomeTaxNumber", "Email", "LedgerMobile", "IsBillWiseOn",
                   "TaxType", "GSTDutyHead", "MasterId", "GUID", "LedGSTRegDetails.*"],
    },
    "group": {
        "type": "Group", "tag": "GROUP",
        "fields": ["Name", "Parent", "IsRevenue", "IsDeemedPositive", "AffectsGrossProfit", "OpeningBalance",
                   "ClosingBalance", "MasterId", "GUID"],
    },
    "stock_item": {
        "type": "StockItem", "tag": "STOCKITEM",
        "fields": ["Name", "Parent", "Category", "BaseUnits", "OpeningBalance", "OpeningRate", "OpeningValue",
                   "ClosingBalance", "ClosingRate", "ClosingValue", "ReorderLevel", "MasterId", "GUID",
                   "GSTDetails.*", "HSNDetails.*"],
    },
    "stock_group": {"type": "StockGroup", "tag": "STOCKGROUP", "fields": ["Name", "Parent", "MasterId", "GUID"]},
    "stock_category": {"type": "StockCategory", "tag": "STOCKCATEGORY",
                       "fields": ["Name", "Parent", "MasterId", "GUID"]},
    "unit": {"type": "Unit", "tag": "UNIT",
             "fields": ["Name", "OriginalName", "IsSimpleUnit", "DecimalPlaces", "BaseUnits", "AdditionalUnits",
                        "Conversion", "GSTRepUOM", "MasterId", "GUID"]},
    "godown": {"type": "Godown", "tag": "GODOWN", "fields": ["Name", "Parent", "Address", "MasterId", "GUID"]},
    "cost_centre": {"type": "CostCentre", "tag": "COSTCENTRE",
                    "fields": ["Name", "Parent", "Category", "ForPayroll", "MasterId", "GUID"]},
    "cost_category": {"type": "CostCategory", "tag": "COSTCATEGORY",
                      "fields": ["Name", "AllocateRevenue", "AllocateNonRevenue", "MasterId", "GUID"]},
    "voucher_type": {"type": "VoucherType", "tag": "VOUCHERTYPE",
                     "fields": ["Name", "Parent", "NumberingMethod", "IsActive", "AffectsStock", "MasterId",
                                "GUID"]},
    "currency": {"type": "Currency", "tag": "CURRENCY",
                 "fields": ["Name", "OriginalName", "MailingName", "ExpandedSymbol", "DecimalSymbol",
                            "DecimalPlaces", "MasterId", "GUID"]},
    "budget": {"type": "Budget", "tag": "BUDGET", "fields": ["Name", "Parent", "FromDate", "ToDate", "GUID"]},
    "employee": {
        "type": "CostCentre", "tag": "COSTCENTRE",
        "fields": ["Name", "Parent", "Category", "ForPayroll", "IsEmployeeGroup", "Designation", "Function",
                   "Location", "DateOfJoin", "DeactivationDate", "EmployeeNumber", "PANNumber", "MasterId",
                   "GUID"],
        "filter": "$ForPayroll AND NOT $IsEmployeeGroup",
        "extra": {"FORPAYROLL": "Yes", "ISEMPLOYEEGROUP": "No"},
    },
    "employee_group": {
        "type": "CostCentre", "tag": "COSTCENTRE",
        "fields": ["Name", "Parent", "Category", "ForPayroll", "IsEmployeeGroup", "MasterId", "GUID"],
        "filter": "$ForPayroll AND $IsEmployeeGroup",
        "extra": {"FORPAYROLL": "Yes", "ISEMPLOYEEGROUP": "Yes"},
    },
    "pay_head": {
        "type": "Ledger", "tag": "LEDGER",
        "fields": ["Name", "Parent", "PayType", "IncomeType", "CalculationType", "LeaveType", "MasterId",
                   "GUID"],
        "filter": "NOT $$IsEmpty:$PayType",
    },
    "attendance_type": {"type": "AttendanceType", "tag": "ATTENDANCETYPE",
                        "fields": ["Name", "Parent", "AttendanceProductionType", "BaseUnits", "MasterId",
                                   "GUID"]},
    "company": {"type": "Company", "tag": "COMPANY",
                "fields": ["Name", "StartingFrom", "BooksFrom", "EndingAt", "CompanyNumber", "GUID"]},
}

_BALANCE_TAGS = {"OPENINGBALANCE", "CLOSINGBALANCE"}
_VALUE_TAGS = {"OPENINGVALUE", "CLOSINGVALUE"}
_RATE_TAGS = {"OPENINGRATE", "CLOSINGRATE"}
_DATE_TAGS = {"STARTINGFROM", "BOOKSFROM", "ENDINGAT", "FROMDATE", "TODATE", "DATEOFJOIN", "DEACTIVATIONDATE",
              "APPLICABLEFROM"}
_BOOL_TAGS = {"ISREVENUE", "ISDEEMEDPOSITIVE", "AFFECTSGROSSPROFIT", "ISBILLWISEON", "FORPAYROLL",
              "ISEMPLOYEEGROUP", "ISSIMPLEUNIT", "ISACTIVE", "AFFECTSSTOCK", "ALLOCATEREVENUE",
              "ALLOCATENONREVENUE"}
_STOCK_TYPES = {"stock_item"}


def master_spec(master_type: str) -> dict[str, Any]:
    key = master_type.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"ledgers": "ledger", "groups": "group", "item": "stock_item", "stockitem": "stock_item",
               "stock_items": "stock_item", "items": "stock_item", "stockgroup": "stock_group",
               "units": "unit", "godowns": "godown", "location": "godown", "cost_center": "cost_centre",
               "costcentre": "cost_centre", "cost_centres": "cost_centre", "vouchertype": "voucher_type",
               "voucher_types": "voucher_type", "employees": "employee", "payhead": "pay_head",
               "currencies": "currency", "companies": "company", "stock_categories": "stock_category",
               "cost_categories": "cost_category", "budgets": "budget"}
    key = aliases.get(key, key)
    if key not in MASTER_TYPES:
        raise TallyError(f"Unknown master type '{master_type}'. Use one of: {', '.join(MASTER_TYPES)}.")
    return {"key": key, **MASTER_TYPES[key]}


def normalise_master(key: str, element: ET.Element) -> dict[str, Any]:
    """One master object -> a flat, readable dictionary."""
    row: dict[str, Any] = {"name": element.get("NAME") or tx.text_of(element, "NAME")}
    for child in element:
        tag = child.tag
        text = (child.text or "").strip()
        if len(child) or not text or tag in ("NAME", "ALTERID", "RAJISACTIVE"):
            continue
        field = _snake(tag)
        if tag in _BALANCE_TAGS:
            if key in _STOCK_TYPES:
                qty, unit = tx.quantity(text)
                row[field.replace("balance", "qty")] = float(qty)
                if unit:
                    row["unit"] = unit
            else:
                value = tx.amount(text)
                row[field] = tx.money(abs(value))
                row[field + "_type"] = "Dr" if value < 0 else "Cr" if value > 0 else ""
        elif tag in _VALUE_TAGS:
            row[field] = tx.money(abs(tx.amount(text)))
        elif tag in _RATE_TAGS:
            row[field] = tx.money(tx.rate(text)[0])
        elif tag in _DATE_TAGS:
            row[field] = tx.iso_date(text)
        elif tag in _BOOL_TAGS:
            row[field] = tx.yes(text)
        else:
            row[field] = text
    if key in ("ledger", "pay_head"):
        gstin = row.pop("party_gstin", "") or _last_text(element, "GSTIN")
        if gstin:
            row["gstin"] = gstin
        if row.get("income_tax_number"):
            row["pan"] = row.pop("income_tax_number")
        if row.get("led_state_name"):
            row["state"] = row.pop("led_state_name")
        elif _last_text(element, "PLACEOFSUPPLY"):
            row["state"] = _last_text(element, "PLACEOFSUPPLY")
    if key == "stock_item":
        hsn = _last_text(element, "HSNCODE")
        if hsn:
            row["hsn"] = hsn
        rate = gst_rate_of(element)
        if rate is not None:
            row["gst_rate"] = rate
    return row


def gst_rate_of(element: ET.Element) -> float | None:
    """Total GST % of a stock item: the IGST rate, or CGST + SGST if IGST is not stated."""
    heads: dict[str, Decimal] = {}
    for detail in element.iter("RATEDETAILS.LIST"):
        head = tx.text_of(detail, "GSTRATEDUTYHEAD").lower()
        value = tx.amount(tx.text_of(detail, "GSTRATE"))
        if head and value:
            heads[head] = value
    for name in ("integrated tax", "igst"):
        if name in heads:
            return float(heads[name])
    split = [v for k, v in heads.items() if k in ("central tax", "cgst", "state tax", "sgst/utgst", "sgst")]
    return float(sum(split)) if split else None


def _last_text(element: ET.Element, tag: str) -> str:
    found = ""
    for item in element.iter(tag):
        if item.text and item.text.strip():
            found = item.text.strip()
    return found


def _snake(tag: str) -> str:
    known = {
        "OPENINGBALANCE": "opening_balance", "CLOSINGBALANCE": "closing_balance", "PARTYGSTIN": "party_gstin",
        "GSTREGISTRATIONTYPE": "gst_registration_type", "LEDSTATENAME": "led_state_name",
        "COUNTRYNAME": "country", "INCOMETAXNUMBER": "income_tax_number", "LEDGERMOBILE": "mobile",
        "ISBILLWISEON": "bill_wise", "TAXTYPE": "tax_type", "GSTDUTYHEAD": "gst_duty_head",
        "MASTERID": "master_id", "ISREVENUE": "is_revenue", "ISDEEMEDPOSITIVE": "is_deemed_positive",
        "AFFECTSGROSSPROFIT": "affects_gross_profit", "BASEUNITS": "unit", "OPENINGRATE": "opening_rate",
        "OPENINGVALUE": "opening_value", "CLOSINGRATE": "closing_rate", "CLOSINGVALUE": "closing_value",
        "REORDERLEVEL": "reorder_level", "ORIGINALNAME": "formal_name", "ISSIMPLEUNIT": "is_simple",
        "DECIMALPLACES": "decimal_places", "ADDITIONALUNITS": "additional_units", "GSTREPUOM": "uqc",
        "FORPAYROLL": "for_payroll", "ISEMPLOYEEGROUP": "is_employee_group", "DATEOFJOIN": "date_of_join",
        "DEACTIVATIONDATE": "date_of_leaving", "EMPLOYEENUMBER": "employee_number", "PANNUMBER": "pan",
        "PAYTYPE": "pay_type", "INCOMETYPE": "income_type", "CALCULATIONTYPE": "calculation_type",
        "LEAVETYPE": "leave_type", "NUMBERINGMETHOD": "numbering_method", "ISACTIVE": "is_active",
        "AFFECTSSTOCK": "affects_stock", "MAILINGNAME": "mailing_name", "EXPANDEDSYMBOL": "symbol",
        "DECIMALSYMBOL": "decimal_symbol", "STARTINGFROM": "financial_year_from", "BOOKSFROM": "books_from",
        "ENDINGAT": "ending_at", "COMPANYNUMBER": "company_number", "FROMDATE": "from_date",
        "TODATE": "to_date", "ALLOCATEREVENUE": "allocate_revenue",
        "ALLOCATENONREVENUE": "allocate_non_revenue",
        "ATTENDANCEPRODUCTIONTYPE": "attendance_type",
    }
    return known.get(tag, tag.lower())


def list_masters(master_type: str, *, parent: str | None = None, search: str | None = None,
                 company: str | None = None, from_date: Any = None, to_date: Any = None,
                 extra_fields: list[str] | None = None) -> list[dict[str, Any]]:
    """Read every master of one type as dictionaries, optionally under a parent."""
    spec = master_spec(master_type)
    filters = {"RajTypeFilter": spec["filter"]} if spec.get("filter") else None
    elements = tx.collection(
        spec["type"], list(spec["fields"]) + list(extra_fields or []),
        child_of=parent or None, filters=filters, company=company, from_date=from_date, to_date=to_date,
    )
    rows = [normalise_master(spec["key"], e) for e in elements]
    if search:
        needle = search.strip().lower()
        rows = [r for r in rows if needle in r["name"].lower()]
    rows.sort(key=lambda r: r["name"].lower())
    return rows


def group_tree(company: str | None = None) -> dict[str, dict[str, Any]]:
    """Every group with its primary (top-level) group resolved."""
    groups = {g["name"]: g for g in list_masters("group", company=company)}
    for group in groups.values():
        top, seen = group, set()
        while top.get("parent") and top["parent"] in groups and top["name"] not in seen:
            seen.add(top["name"])
            top = groups[top["parent"]]
        group["primary_group"] = top["name"]
    return groups


def primary_of(group_name: str, groups: dict[str, dict[str, Any]]) -> str:
    return groups.get(group_name, {}).get("primary_group", group_name)


# --------------------------------------------------------------------------
# Vouchers
# --------------------------------------------------------------------------

VOUCHER_FETCH = [
    "*", "Date", "VoucherTypeName", "VoucherNumber", "PartyLedgerName", "Narration", "Reference",
    "ReferenceDate", "MasterId", "GUID", "AlterId", "IsCancelled", "IsOptional", "IsInvoice", "PlaceOfSupply",
    "PartyGSTIN", "AllLedgerEntries.*", "LedgerEntries.*", "AllInventoryEntries.*", "InventoryEntries.*",
    "InventoryEntriesIn.*", "InventoryEntriesOut.*",
]

# Tally's own "is this a kind of ..." functions, so user-defined voucher types are included.
_BASE_TYPE_TEST = {
    "sales": "$$IsSales", "purchase": "$$IsPurchase", "payment": "$$IsPayment", "receipt": "$$IsReceipt",
    "contra": "$$IsContra", "journal": "$$IsJournal", "credit note": "$$IsCreditNote",
    "debit note": "$$IsDebitNote",
}


def parse_voucher(element: ET.Element, include_entries: bool = True) -> dict[str, Any]:
    """One <VOUCHER> element -> a plain dictionary with ledger entries and item lines."""
    voucher: dict[str, Any] = {
        "date": tx.iso_date(tx.text_of(element, "DATE")),
        "type": tx.text_of(element, "VOUCHERTYPENAME") or element.get("VCHTYPE", ""),
        "number": tx.text_of(element, "VOUCHERNUMBER"),
        "party": tx.text_of(element, "PARTYLEDGERNAME"),
        "narration": tx.text_of(element, "NARRATION"),
        "reference": tx.text_of(element, "REFERENCE"),
        "master_id": tx.text_of(element, "MASTERID"),
        "guid": tx.text_of(element, "GUID") or element.get("REMOTEID", ""),
        "cancelled": tx.yes(tx.text_of(element, "ISCANCELLED")),
        "optional": tx.yes(tx.text_of(element, "ISOPTIONAL")),
    }
    for tag, key in (("PARTYGSTIN", "party_gstin"), ("PLACEOFSUPPLY", "place_of_supply"),
                     ("ALTERID", "alter_id")):
        value = tx.text_of(element, tag)
        if value:
            voucher[key] = value

    entries: list[dict[str, Any]] = []
    for tag in ("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST"):
        for entry in element.findall(tag):
            ledger = tx.text_of(entry, "LEDGERNAME")
            if not ledger:
                continue
            value = tx.amount(tx.text_of(entry, "AMOUNT"))
            row: dict[str, Any] = {"ledger": ledger, **tx.dr_cr(value)}
            bills = [
                {"name": tx.text_of(b, "NAME"), "type": tx.text_of(b, "BILLTYPE"),
                 **tx.dr_cr(tx.amount(tx.text_of(b, "AMOUNT")))}
                for b in entry.findall("BILLALLOCATIONS.LIST") if tx.text_of(b, "NAME")
            ]
            if bills:
                row["bills"] = bills
            bank = [
                {"instrument_number": tx.text_of(b, "INSTRUMENTNUMBER"),
                 "instrument_date": tx.iso_date(tx.text_of(b, "INSTRUMENTDATE")),
                 "transaction_type": tx.text_of(b, "TRANSACTIONTYPE"),
                 "bank_date": tx.iso_date(tx.text_of(b, "BANKERSDATE")),
                 "amount": tx.money(abs(tx.amount(tx.text_of(b, "AMOUNT"))))}
                for b in entry.findall("BANKALLOCATIONS.LIST")
            ]
            if bank:
                row["bank"] = bank
            centres = [
                {"category": tx.text_of(cat, "CATEGORY"), "cost_centre": tx.text_of(cc, "NAME"),
                 "amount": tx.money(abs(tx.amount(tx.text_of(cc, "AMOUNT"))))}
                for cat in entry.findall("CATEGORYALLOCATIONS.LIST")
                for cc in cat.findall("COSTCENTREALLOCATIONS.LIST")
            ]
            if centres:
                row["cost_centres"] = centres
            entries.append(row)

    items: list[dict[str, Any]] = []
    for tag in ("ALLINVENTORYENTRIES.LIST", "INVENTORYENTRIES.LIST", "INVENTORYENTRIESIN.LIST",
                "INVENTORYENTRIESOUT.LIST"):
        for line in element.findall(tag):
            name = tx.text_of(line, "STOCKITEMNAME")
            if not name:
                continue
            qty, unit = tx.quantity(tx.text_of(line, "BILLEDQTY") or tx.text_of(line, "ACTUALQTY"))
            price, _ = tx.rate(tx.text_of(line, "RATE"))
            value = tx.amount(tx.text_of(line, "AMOUNT"))
            item: dict[str, Any] = {"item": name, "qty": float(qty), "unit": unit, "rate": tx.money(price),
                                    "amount": tx.money(abs(value))}
            if tag.endswith("IN.LIST"):
                item["direction"] = "in"
            elif tag.endswith("OUT.LIST"):
                item["direction"] = "out"
            else:
                item["direction"] = "in" if tx.yes(tx.text_of(line, "ISDEEMEDPOSITIVE")) else "out"
            godown = tx.text_of(line.find("BATCHALLOCATIONS.LIST"), "GODOWNNAME")
            if godown:
                item["godown"] = godown
            discount = tx.text_of(line, "DISCOUNT")
            if discount:
                item["discount_percent"] = float(tx.amount(discount))
            items.append(item)
            # the sales / purchase ledger of an item line lives inside the line
            for alloc in line.findall("ACCOUNTINGALLOCATIONS.LIST"):
                ledger = tx.text_of(alloc, "LEDGERNAME")
                if ledger:
                    entries.append({"ledger": ledger, **tx.dr_cr(tx.amount(tx.text_of(alloc, "AMOUNT"))),
                                    "from_item": name})

    debit = sum(Decimal(str(e["dr"])) for e in entries)
    credit = sum(Decimal(str(e["cr"])) for e in entries)
    voucher["amount"] = tx.money(max(debit, credit)) if entries else tx.money(
        abs(tx.amount(tx.text_of(element, "AMOUNT"))))
    if include_entries:
        voucher["entries"] = entries
        if items:
            voucher["items"] = items
    return voucher


def _matches_type(voucher_type_name: str, wanted: str) -> bool:
    return voucher_type_name.strip().lower() == wanted.strip().lower()


def fetch_vouchers(from_date: Any, to_date: Any, *, voucher_type: str | None = None,
                   company: str | None = None, include_entries: bool = True,
                   source: str = "auto") -> list[dict[str, Any]]:
    """Read vouchers for a period.

    ``source``:
      * ``collection`` - a TDL collection (fast, filtered inside Tally)
      * ``daybook``    - Tally's Day Book export (complete voucher XML)
      * ``auto``       - collection first; Day Book if entries did not come through
    """
    start, end = tx.parse_date(from_date), tx.parse_date(to_date)
    vouchers: list[dict[str, Any]] = []
    if source in ("auto", "collection"):
        filters: dict[str, str] = {}  # the period itself is set through SVFROMDATE / SVTODATE
        if voucher_type:
            test = _BASE_TYPE_TEST.get(voucher_type.strip().lower())
            filters["RajVchType"] = (f"{test}:$VoucherTypeName" if test
                                     else f"$VoucherTypeName = {tx.tdl_string(voucher_type)}")
        elements = tx.collection("Voucher", VOUCHER_FETCH, filters=filters or None, company=company,
                                 from_date=start, to_date=end)
        vouchers = [parse_voucher(e, True) for e in elements if e.tag == "VOUCHER"]
        if source == "auto" and vouchers and not any(v["entries"] for v in vouchers):
            vouchers = []
            source = "daybook"
    if source == "daybook":
        root = tx.native_report("Day Book", company=company, from_date=start, to_date=end)
        vouchers = [parse_voucher(e, True) for e in root.iter("VOUCHER")]
        if voucher_type:
            base = voucher_type.strip().lower()
            vouchers = [v for v in vouchers if _matches_type(v["type"], base)]
    # belt and braces: enforce the period here too
    if start and end:
        low, high = start.isoformat(), end.isoformat()
        vouchers = [v for v in vouchers if not v["date"] or low <= v["date"] <= high]
    vouchers.sort(key=lambda v: (v["date"], v["type"], _number_key(v["number"])))
    if not include_entries:
        for voucher in vouchers:
            voucher.pop("entries", None)
            voucher.pop("items", None)
    return vouchers


def _number_key(number: str) -> tuple[int, str]:
    digits = "".join(ch for ch in number if ch.isdigit())
    return (int(digits) if digits else 0, number)


def find_voucher(*, master_id: str | None = None, guid: str | None = None, number: str | None = None,
                 voucher_type: str | None = None, date: Any = None,
                 company: str | None = None) -> ET.Element:
    """Locate exactly one voucher and return its full XML element."""
    if guid:
        formula = f"$GUID = {tx.tdl_string(guid)}"
    elif master_id:
        formula = f"$MasterId = {int(str(master_id).strip())}"
    elif number and voucher_type:
        formula = f"$VoucherNumber = {tx.tdl_string(number)} AND $VoucherTypeName = {tx.tdl_string(voucher_type)}"
    else:
        raise TallyError("Identify the voucher by master_id, or guid, or number + voucher_type (+ date).")
    when = tx.parse_date(date)
    start = when or dt.date(1990, 4, 1)
    end = when or dt.date(2099, 3, 31)
    elements = [e for e in tx.collection("Voucher", VOUCHER_FETCH, filters={"RajFind": formula},
                                         company=company, from_date=start, to_date=end)
                if e.tag == "VOUCHER"]
    # Tally ignores a filter it cannot evaluate, so check the match ourselves.
    matches = []
    for element in elements:
        if guid and (tx.text_of(element, "GUID") or element.get("REMOTEID", "")) != guid:
            continue
        if master_id and not guid and tx.text_of(element, "MASTERID") != str(master_id).strip():
            continue
        if number and not (guid or master_id):
            if tx.text_of(element, "VOUCHERNUMBER") != str(number):
                continue
            if not _matches_type(tx.text_of(element, "VOUCHERTYPENAME"), voucher_type or ""):
                continue
        matches.append(element)
    if not matches:
        raise TallyError("No voucher matched. Check the id / number, voucher type, date and company.")
    if len(matches) > 1:
        raise TallyError(
            f"{len(matches)} vouchers matched. Add the date, or use master_id from tally_list_vouchers.")
    return matches[0]
