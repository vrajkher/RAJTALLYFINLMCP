"""GST working papers from the books: registers, tax summary, GSTR-1 data, HSN summary, GSTIN check.

These are computed from vouchers so every figure can be traced to an entry. They are working data for
review, not a filed return; Tally's own return computation is available via tally_native_report.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .. import core
from .. import tally_xml as tx
from ..tally_xml import TallyError
from . import READ
from .reports import books

STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand",
    "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman & Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory", "99": "Centre Jurisdiction",
}
_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_SHAPE = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]")


def check_gstin(gstin: str) -> dict[str, Any]:
    """Validate a GSTIN's format and check digit, and read its state and PAN."""
    value = (gstin or "").strip().upper()
    result: dict[str, Any] = {"gstin": value, "valid": False}
    if len(value) != 15:
        result["problem"] = f"A GSTIN has 15 characters; this has {len(value)}."
        return result
    if not _GSTIN_SHAPE.fullmatch(value):
        result["problem"] = ("Format should be 2-digit state code + 10-character PAN + entity number + 'Z' "
                             "+ check character.")
        return result
    total = 0
    for index, char in enumerate(value[:14]):
        product = _GSTIN_CHARS.index(char) * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    expected = _GSTIN_CHARS[(36 - total % 36) % 36]
    result.update(state_code=value[:2], state=STATE_CODES.get(value[:2], "Unknown state code"), pan=value[2:12])
    if value[:2] not in STATE_CODES:
        result["problem"] = f"State code {value[:2]} is not a GST state code."
    elif expected != value[14]:
        result["problem"] = f"Check character should be '{expected}', found '{value[14]}'. Likely a typing error."
    else:
        result["valid"] = True
    return result


def tax_head(ledger: dict[str, Any]) -> str | None:
    """Which tax a ledger carries: cgst, sgst, igst, cess, tds, tcs, other_tax - or None if not a tax ledger."""
    name = ledger["name"].lower()
    duty = (ledger.get("gst_duty_head") or "").lower()
    kind = (ledger.get("tax_type") or "").lower()
    if "duties & taxes" not in ledger["chain"]:
        return None
    if kind == "tds" or re.search(r"\btds\b", name):
        return "tds"
    if kind == "tcs" or re.search(r"\btcs\b", name):
        return "tcs"
    if duty in ("central tax", "cgst") or "cgst" in name or "central tax" in name:
        return "cgst"
    if duty in ("state tax", "sgst/utgst", "sgst", "ut tax") or "sgst" in name or "utgst" in name \
            or "state tax" in name:
        return "sgst"
    if duty in ("integrated tax", "igst") or "igst" in name or "integrated tax" in name:
        return "igst"
    if duty == "cess" or "cess" in name:
        return "cess"
    return "other_tax"


_ROUND_OFF = re.compile(r"round(ed|ing)?[\s\-_/]*off|\brounding\b", re.IGNORECASE)


def is_round_off(name: str) -> bool:
    return bool(_ROUND_OFF.search(name))


def base_types(company: str | None) -> dict[str, str]:
    """Voucher type name -> its base type (so 'Sales - Export' counts as Sales)."""
    types = {row["name"]: row.get("parent", "") for row in core.list_masters("voucher_type", company=company)}
    resolved = {}
    for name in types:
        top, seen = name, set()
        while types.get(top) and types[top] != top and top not in seen and types[top] in types:
            seen.add(top)
            top = types[top]
        resolved[name.lower()] = top.lower()
    return resolved


def invoice_rows(vouchers: list[dict[str, Any]], ledgers: dict[str, dict[str, Any]], outward: bool
                 ) -> list[dict[str, Any]]:
    """Split each invoice into taxable value, tax by head and total."""
    rows = []
    for voucher in vouchers:
        if voucher["cancelled"] or voucher["optional"]:
            continue
        taxable = round_off = Decimal(0)
        taxes: dict[str, Decimal] = {}
        party_total = Decimal(0)
        party = voucher["party"]
        for entry in voucher["entries"]:
            info = ledgers.get(entry["ledger"].lower())
            value = Decimal(str(entry["cr"])) - Decimal(str(entry["dr"]))  # credit positive
            if not outward:
                value = -value
            head = tax_head(info) if info else None
            if head:
                taxes[head] = taxes.get(head, Decimal(0)) + value
            elif is_round_off(entry["ledger"]):
                round_off += value
            elif info and info["nature"] in ("income", "expense"):
                taxable += value
            elif info and ("sundry debtors" in info["chain"] or "sundry creditors" in info["chain"]
                           or entry["ledger"] == party):
                party_total += -value
                party = party or entry["ledger"]
        info = ledgers.get(party.lower(), {})
        gstin = voucher.get("party_gstin") or info.get("gstin", "")
        gst = sum((v for k, v in taxes.items() if k in ("cgst", "sgst", "igst", "cess")), Decimal(0))
        row = {"date": voucher["date"], "type": voucher["type"], "number": voucher["number"],
               "reference": voucher["reference"], "party": party, "gstin": gstin,
               "place_of_supply": voucher.get("place_of_supply") or info.get("state", ""),
               "taxable_value": tx.money(taxable)}
        for head in ("cgst", "sgst", "igst", "cess"):
            row[head] = tx.money(taxes.get(head, Decimal(0)))
        for head in ("tds", "tcs", "other_tax"):
            if taxes.get(head):
                row[head] = tx.money(taxes[head])
        if round_off:
            row["round_off"] = tx.money(round_off)
        row["gst_rate"] = float((gst * 100 / taxable).quantize(Decimal("0.1"))) if taxable else 0.0
        row["total"] = tx.money(party_total or voucher["amount"])
        row["master_id"] = voucher["master_id"]
        rows.append(row)
    return rows


def _ledger_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {l["name"].lower(): l for l in data["ledgers"]}


def _totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {key: tx.money(sum(Decimal(str(r.get(key, 0))) for r in rows))
            for key in ("taxable_value", "cgst", "sgst", "igst", "cess", "total")}


def register(mcp: Any) -> None:

    @mcp.tool(annotations=READ)
    def tally_validate_gstin(gstin: str) -> dict[str, Any]:
        """Check a GSTIN offline: length, format and check digit; returns the state and the PAN inside it.
        This catches typing errors. It does not confirm the registration is active on the GST portal."""
        return check_gstin(gstin)

    @mcp.tool(annotations=READ)
    def tally_register(kind: str = "sales", from_date: str | None = None, to_date: str | None = None,
                       party: str | None = None, limit: int = 500, offset: int = 0,
                       company: str | None = None) -> dict[str, Any]:
        """Sales / purchase register: one row per invoice with party, GSTIN, taxable value, CGST, SGST,
        IGST, cess and total - plus totals for the period.
        kind: sales, purchase, credit_note or debit_note (notes are shown at their own value)."""
        key = kind.strip().lower().replace(" ", "_")
        names = {"sales": ("Sales", True), "purchase": ("Purchase", False),
                 "credit_note": ("Credit Note", False), "debit_note": ("Debit Note", True)}
        if key not in names:
            raise TallyError("kind must be sales, purchase, credit_note or debit_note.")
        start, end = tx.period(from_date, to_date)
        ledgers = _ledger_map(books(start, end, company))
        vouchers = core.fetch_vouchers(start, end, voucher_type=names[key][0], company=company)
        rows = invoice_rows(vouchers, ledgers, outward=names[key][1])
        if party:
            rows = [r for r in rows if r["party"].lower() == party.strip().lower()]
        return {"register": names[key][0], "from_date": start.isoformat(), "to_date": end.isoformat(),
                "totals": _totals(rows), **core.page(rows, limit, offset, "invoices")}

    @mcp.tool(annotations=READ)
    def tally_gst_summary(from_date: str | None = None, to_date: str | None = None,
                          company: str | None = None) -> dict[str, Any]:
        """GST position for a period, from the books: output tax on sales, input tax on purchases, the
        net payable by head (CGST / SGST / IGST / cess), other movements such as tax payments and
        adjustments, and the closing balance of each GST ledger. Use it to review a month before
        filing GSTR-3B. Dates blank = current financial year; for a month pass both dates."""
        start, end = tx.period(from_date, to_date)
        data = books(start, end, company)
        ledgers = _ledger_map(data)
        bases = base_types(company)
        heads = ("cgst", "sgst", "igst", "cess")
        zero = lambda: {h: Decimal(0) for h in heads}  # noqa: E731
        output, credit, other = zero(), zero(), zero()
        turnover = {"sales": Decimal(0), "purchases": Decimal(0)}
        for voucher in core.fetch_vouchers(start, end, company=company):
            if voucher["cancelled"] or voucher["optional"]:
                continue
            base = bases.get(voucher["type"].lower(), voucher["type"].lower())
            for entry in voucher["entries"]:
                info = ledgers.get(entry["ledger"].lower())
                if not info:
                    continue
                value = Decimal(str(entry["cr"])) - Decimal(str(entry["dr"]))  # credit positive
                head = tax_head(info)
                if head in heads:
                    if base in ("sales", "credit note"):
                        output[head] += value
                    elif base in ("purchase", "debit note"):
                        credit[head] += -value
                    else:
                        other[head] += -value
                elif head is None and info["nature"] in ("income", "expense") \
                        and not is_round_off(entry["ledger"]):
                    if base in ("sales", "credit note"):
                        turnover["sales"] += value
                    elif base in ("purchase", "debit note"):
                        turnover["purchases"] += -value
        rows = [{"head": h.upper(), "output_tax": tx.money(output[h]), "input_tax_credit": tx.money(credit[h]),
                 "net_payable": tx.money(output[h] - credit[h]),
                 "payments_and_adjustments": tx.money(other[h])} for h in heads]
        balances = []
        for ledger in data["ledgers"]:
            if tax_head(ledger) in heads and (ledger["closing"] or ledger["opening"]):
                closing = tx.dr_cr(ledger["closing"])
                balances.append({"ledger": ledger["name"], "head": tax_head(ledger).upper(),
                                 "closing_dr": closing["dr"], "closing_cr": closing["cr"]})
        return {"from_date": start.isoformat(), "to_date": end.isoformat(),
                "taxable_sales": tx.money(turnover["sales"]), "taxable_purchases": tx.money(turnover["purchases"]),
                "total_output_tax": tx.money(sum(output.values())),
                "total_input_tax_credit": tx.money(sum(credit.values())),
                "net_gst_payable": tx.money(sum(output.values()) - sum(credit.values())),
                "by_head": rows, "gst_ledger_balances": balances,
                "note": "Credit notes are netted against sales and debit notes against purchases. "
                        "Closing Cr = payable, Dr = credit available."}

    @mcp.tool(annotations=READ)
    def tally_gstr1(from_date: str | None = None, to_date: str | None = None, company: str | None = None
                    ) -> dict[str, Any]:
        """GSTR-1 working data from the books: B2B invoices (registered buyers), B2C sales by state,
        credit notes to registered buyers, totals, and invoices that need attention (missing or
        invalid GSTIN, no place of supply). For Tally's own computation use
        tally_native_report('GSTR-1')."""
        start, end = tx.period(from_date, to_date)
        ledgers = _ledger_map(books(start, end, company))
        sales = invoice_rows(core.fetch_vouchers(start, end, voucher_type="Sales", company=company), ledgers, True)
        notes = invoice_rows(core.fetch_vouchers(start, end, voucher_type="Credit Note", company=company),
                             ledgers, False)
        b2b = [r for r in sales if r["gstin"]]
        b2c = [r for r in sales if not r["gstin"]]
        by_state: dict[tuple[str, float], dict[str, Any]] = {}
        for row in b2c:
            key = (row["place_of_supply"] or "Not stated", row["gst_rate"])
            state = by_state.setdefault(key, {"place_of_supply": key[0], "gst_rate": key[1], "invoices": 0,
                                              "rows": []})
            state["invoices"] += 1
            state["rows"].append(row)
        b2c_summary = [{"place_of_supply": s["place_of_supply"], "gst_rate": s["gst_rate"],
                        "invoices": s["invoices"], **_totals(s["rows"])} for s in by_state.values()]
        attention = []
        for row in sales + notes:
            if row["gstin"] and not check_gstin(row["gstin"])["valid"]:
                attention.append({"number": row["number"], "party": row["party"],
                                  "issue": f"GSTIN {row['gstin']} fails the check-digit test"})
            if not row["place_of_supply"]:
                attention.append({"number": row["number"], "party": row["party"],
                                  "issue": "No place of supply / party state"})
            if not row["number"]:
                attention.append({"number": "", "party": row["party"], "issue": "Invoice has no number"})
        return {"from_date": start.isoformat(), "to_date": end.isoformat(),
                "b2b": {"count": len(b2b), "totals": _totals(b2b), "invoices": b2b[:1000]},
                "b2c": {"count": len(b2c), "totals": _totals(b2c), "by_state_and_rate": b2c_summary},
                "credit_notes_registered": {"count": len([n for n in notes if n["gstin"]]),
                                            "totals": _totals([n for n in notes if n["gstin"]]),
                                            "notes": [n for n in notes if n["gstin"]][:500]},
                "credit_notes_unregistered": {"count": len([n for n in notes if not n["gstin"]]),
                                              "totals": _totals([n for n in notes if not n["gstin"]])},
                "needs_attention": attention}

    @mcp.tool(annotations=READ)
    def tally_hsn_summary(kind: str = "sales", from_date: str | None = None, to_date: str | None = None,
                          company: str | None = None) -> dict[str, Any]:
        """HSN-wise summary of goods sold (or purchased): quantity, taxable value and GST rate per HSN
        code, taken from invoice item lines and each item's HSN. Lists items that have no HSN code.
        kind: sales or purchase."""
        start, end = tx.period(from_date, to_date)
        voucher_type = "Purchase" if kind.strip().lower().startswith("p") else "Sales"
        items = {row["name"].lower(): row for row in core.list_masters("stock_item", company=company)}
        summary: dict[tuple[str, float, str], dict[str, Decimal]] = {}
        missing: set[str] = set()
        for voucher in core.fetch_vouchers(start, end, voucher_type=voucher_type, company=company):
            if voucher["cancelled"] or voucher["optional"]:
                continue
            for line in voucher.get("items", []):
                master = items.get(line["item"].lower(), {})
                hsn = master.get("hsn", "")
                if not hsn:
                    missing.add(line["item"])
                key = (hsn or "(no HSN)", float(master.get("gst_rate") or 0), line["unit"] or master.get("unit", ""))
                row = summary.setdefault(key, {"qty": Decimal(0), "taxable": Decimal(0)})
                row["qty"] += Decimal(str(line["qty"]))
                row["taxable"] += Decimal(str(line["amount"]))
        rows = [{"hsn": hsn, "gst_rate": rate, "unit": unit, "qty": float(v["qty"]),
                 "taxable_value": tx.money(v["taxable"]),
                 "estimated_tax": tx.money(v["taxable"] * Decimal(str(rate)) / 100)}
                for (hsn, rate, unit), v in sorted(summary.items())]
        result: dict[str, Any] = {"for": voucher_type, "from_date": start.isoformat(), "to_date": end.isoformat(),
                                  "total_taxable_value": tx.money(sum(v["taxable"] for v in summary.values())),
                                  "rows": rows,
                                  "note": "estimated_tax = taxable value x the item's GST rate."}
        if missing:
            result["items_without_hsn"] = sorted(missing)
        return result
