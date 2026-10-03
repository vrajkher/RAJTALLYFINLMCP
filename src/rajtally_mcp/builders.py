"""Builds the XML that Tally imports: masters, accounting vouchers, invoices, stock journals.

Sign rule used everywhere (it is Tally's own):
    debit  -> ISDEEMEDPOSITIVE = Yes, amount NEGATIVE
    credit -> ISDEEMEDPOSITIVE = No,  amount POSITIVE
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from . import config
from . import tally_xml as tx
from .tally_xml import TallyError

TWO = Decimal("0.01")

INVOICE_KINDS = {
    "sales": ("Sales", True),
    "purchase": ("Purchase", False),
    "credit_note": ("Credit Note", False),  # sales return: party credited, stock comes in
    "debit_note": ("Debit Note", True),  # purchase return: party debited, stock goes out
}


def dec(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    return Decimal(str(value))


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def fmt(value: Decimal) -> str:
    return f"{q2(value):.2f}"


def el(tag: str, value: Any) -> str:
    """One simple element; nothing at all if the value is blank."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    return f"<{tag}>{escape(str(value))}</{tag}>"


def extra_xml(fields: dict[str, Any] | None) -> str:
    """Free-form extra Tally tags supplied by the caller."""
    return "".join(tx.to_xml(key, value) for key, value in (fields or {}).items())


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------

def master_xml(tag: str, name: str, body: str = "", action: str = "Create", new_name: str | None = None,
               alias: str | None = None) -> str:
    shown = new_name or name
    names = f"<NAME>{escape(shown)}</NAME>" + (f"<NAME>{escape(alias)}</NAME>" if alias else "")
    language = (f'<LANGUAGENAME.LIST><NAME.LIST TYPE="String">{names}</NAME.LIST>'
                "<LANGUAGEID>1033</LANGUAGEID></LANGUAGENAME.LIST>")
    return (f"<{tag} NAME={quoteattr(name)} ACTION={quoteattr(action)}>"
            f"<NAME>{escape(shown)}</NAME>{body}{language}</{tag}>")


def delete_master_xml(tag: str, name: str) -> str:
    return f"<{tag} NAME={quoteattr(name)} ACTION=\"Delete\"></{tag}>"


def ledger_body(*, parent: str | None = None, opening_balance: float | None = None, opening_type: str = "Dr",
                gstin: str | None = None, gst_registration_type: str | None = None, state: str | None = None,
                country: str | None = None, address: list[str] | str | None = None,
                pincode: str | None = None, pan: str | None = None, email: str | None = None,
                mobile: str | None = None, mailing_name: str | None = None, bill_wise: bool | None = None,
                credit_period_days: int | None = None, tax_type: str | None = None,
                gst_duty_head: str | None = None, applicable_from: Any = None,
                fields: dict[str, Any] | None = None) -> str:
    parts = [el("PARENT", parent)]
    if opening_balance:
        sign = Decimal(-1) if opening_type.strip().lower().startswith("d") else Decimal(1)
        parts.append(el("OPENINGBALANCE", fmt(sign * abs(dec(opening_balance)))))
    parts += [el("ISBILLWISEON", bill_wise), el("INCOMETAXNUMBER", pan), el("EMAIL", email),
              el("LEDGERMOBILE", mobile), el("TAXTYPE", tax_type), el("GSTDUTYHEAD", gst_duty_head)]
    if credit_period_days:
        parts.append(el("BILLCREDITPERIOD", f"{int(credit_period_days)} Days"))
    lines = [address] if isinstance(address, str) else list(address or [])
    lines = [line for line in lines if line]
    if gstin and not gst_registration_type:
        gst_registration_type = "Regular"
    # classic tags (Tally.ERP 9 and TallyPrime up to 2.x; newer releases still read them)
    parts += [el("MAILINGNAME", mailing_name), el("LEDSTATENAME", state), el("COUNTRYNAME", country),
              el("PINCODE", pincode), el("PARTYGSTIN", gstin), el("GSTREGISTRATIONTYPE", gst_registration_type)]
    if lines:
        parts.append('<ADDRESS.LIST TYPE="String">' + "".join(el("ADDRESS", line) for line in lines)
                     + "</ADDRESS.LIST>")
    # dated detail lists (TallyPrime 3 onwards)
    if config.get().gst_schema == "prime":
        since = tx.tally_date(applicable_from) if applicable_from else tx.tally_date(tx.financial_year()[0])
        if lines or state or country or pincode or mailing_name:
            parts.append(
                "<LEDMAILINGDETAILS.LIST>" + el("APPLICABLEFROM", since) + el("MAILINGNAME", mailing_name)
                + ('<ADDRESS.LIST TYPE="String">' + "".join(el("ADDRESS", line) for line in lines)
                   + "</ADDRESS.LIST>" if lines else "")
                + el("STATE", state) + el("COUNTRY", country) + el("PINCODE", pincode)
                + "</LEDMAILINGDETAILS.LIST>")
        if gstin or gst_registration_type:
            parts.append(
                "<LEDGSTREGDETAILS.LIST>" + el("APPLICABLEFROM", since)
                + el("GSTREGISTRATIONTYPE", gst_registration_type) + el("PLACEOFSUPPLY", state)
                + el("GSTIN", gstin) + "</LEDGSTREGDETAILS.LIST>")
    parts.append(extra_xml(fields))
    return "".join(parts)


def stock_item_body(*, parent: str | None = None, category: str | None = None, unit: str | None = None,
                    opening_qty: float | None = None, opening_rate: float | None = None,
                    opening_value: float | None = None, godown: str = "Main Location",
                    hsn: str | None = None, gst_rate: float | None = None, taxability: str | None = None,
                    description: str | None = None, applicable_from: Any = None,
                    fields: dict[str, Any] | None = None) -> str:
    parts = [el("PARENT", parent), el("CATEGORY", category), el("BASEUNITS", unit),
             el("DESCRIPTION", description)]
    if opening_qty:
        if not unit:
            raise TallyError("unit is required when an opening quantity is given.")
        qty = dec(opening_qty)
        value = dec(opening_value) if opening_value else qty * dec(opening_rate)
        price = dec(opening_rate) if opening_rate else (value / qty if qty else Decimal(0))
        opening = (el("OPENINGBALANCE", f" {qty.normalize():f} {unit}") + el("OPENINGVALUE", fmt(-abs(value)))
                   + el("OPENINGRATE", f"{fmt(price)}/{unit}"))
        parts.append(opening)
        parts.append("<BATCHALLOCATIONS.LIST>" + el("GODOWNNAME", godown) + el("BATCHNAME", "Primary Batch")
                     + opening + "</BATCHALLOCATIONS.LIST>")
    if hsn or gst_rate is not None or taxability:
        prime = config.get().gst_schema == "prime"
        since = tx.tally_date(applicable_from) if applicable_from else tx.tally_date(tx.financial_year()[0])
        taxability = taxability or ("Taxable" if gst_rate else "Exempt" if gst_rate == 0 else None)
        parts.append(el("GSTAPPLICABLE", "\x04 Applicable").replace("\x04", "&#4;"))
        rates = ""
        if gst_rate is not None:
            total = dec(gst_rate)
            half = total / 2
            heads = ([("CGST", half), ("SGST/UTGST", half), ("IGST", total)] if prime else
                     [("Central Tax", half), ("State Tax", half), ("Integrated Tax", total)])
            rates = "<STATEWISEDETAILS.LIST><STATENAME>&#4; Any</STATENAME>" + "".join(
                "<RATEDETAILS.LIST>" + el("GSTRATEDUTYHEAD", head)
                + el("GSTRATEVALUATIONTYPE", "Based on Value") + el("GSTRATE", f"{value.normalize():f}")
                + "</RATEDETAILS.LIST>" for head, value in heads) + "</STATEWISEDETAILS.LIST>"
        parts.append(
            "<GSTDETAILS.LIST>" + el("APPLICABLEFROM", since) + el("TAXABILITY", taxability)
            + (el("SRCOFGSTDETAILS", "Specify Details Here") if prime else el("HSNCODE", hsn))
            + el("CALCULATIONTYPE", "On Value") + rates + "</GSTDETAILS.LIST>")
        if prime and hsn:
            parts.append("<HSNDETAILS.LIST>" + el("APPLICABLEFROM", since) + el("HSNCODE", hsn)
                         + el("SRCOFHSNDETAILS", "Specify Details Here") + "</HSNDETAILS.LIST>")
    parts.append(extra_xml(fields))
    return "".join(parts)


# --------------------------------------------------------------------------
# Vouchers
# --------------------------------------------------------------------------

def _signed(entry: dict[str, Any]) -> Decimal:
    """dr/cr (or amount + type) -> Tally's signed amount (debit negative)."""
    debit, credit = dec(entry.get("dr")), dec(entry.get("cr"))
    if not debit and not credit and entry.get("amount"):
        kind = str(entry.get("type", "")).strip().lower()
        if kind.startswith("d"):
            debit = abs(dec(entry["amount"]))
        elif kind.startswith("c"):
            credit = abs(dec(entry["amount"]))
        else:
            raise TallyError(f"Entry for '{entry.get('ledger')}': say dr or cr.")
    if debit and credit:
        raise TallyError(f"Entry for '{entry.get('ledger')}' has both dr and cr. Use two entries.")
    if debit < 0 or credit < 0:
        raise TallyError(f"Entry for '{entry.get('ledger')}': amounts must be positive.")
    return q2(credit - debit)


def ledger_entry_xml(entry: dict[str, Any], voucher_date: dt.date, tag: str = "ALLLEDGERENTRIES.LIST",
                     is_party: bool = False) -> str:
    """One ledger line with its optional bill, cost-centre and bank details."""
    ledger = str(entry.get("ledger") or "").strip()
    if not ledger:
        raise TallyError("Every entry needs a ledger name.")
    value = entry["_signed"] if "_signed" in entry else _signed(entry)
    if value == 0:
        raise TallyError(f"Entry for '{ledger}' has no amount.")
    sign = Decimal(-1) if value < 0 else Decimal(1)
    parts = [el("LEDGERNAME", ledger), el("ISDEEMEDPOSITIVE", value < 0)]
    if is_party:
        parts.append(el("ISPARTYLEDGER", True))
    parts.append(el("AMOUNT", fmt(value)))

    for bill in entry.get("bills") or []:
        amount = sign * abs(dec(bill.get("amount"))) if bill.get("amount") else value
        parts.append("<BILLALLOCATIONS.LIST>" + el("NAME", bill.get("name"))
                     + el("BILLTYPE", bill.get("type") or "New Ref")
                     + (el("BILLCREDITPERIOD", f"{int(bill['due_days'])} Days") if bill.get("due_days") else "")
                     + el("AMOUNT", fmt(amount)) + "</BILLALLOCATIONS.LIST>")

    centres = entry.get("cost_centres") or []
    by_category: dict[str, list[dict[str, Any]]] = {}
    for centre in centres:
        by_category.setdefault(centre.get("category") or "Primary Cost Category", []).append(centre)
    for category, rows in by_category.items():
        parts.append("<CATEGORYALLOCATIONS.LIST>" + el("CATEGORY", category) + el("ISDEEMEDPOSITIVE", value < 0)
                     + "".join("<COSTCENTREALLOCATIONS.LIST>" + el("NAME", row.get("name"))
                               + el("AMOUNT", fmt(sign * abs(dec(row.get("amount")))) if row.get("amount")
                                    else fmt(value)) + "</COSTCENTREALLOCATIONS.LIST>" for row in rows)
                     + "</CATEGORYALLOCATIONS.LIST>")

    bank = entry.get("bank")
    if bank:
        instrument_date = tx.tally_date(bank.get("instrument_date")) or tx.tally_date(voucher_date)
        parts.append("<BANKALLOCATIONS.LIST>" + el("DATE", tx.tally_date(voucher_date))
                     + el("INSTRUMENTDATE", instrument_date)
                     + el("TRANSACTIONTYPE", bank.get("transaction_type") or "Cheque")
                     + el("INSTRUMENTNUMBER", bank.get("instrument_number"))
                     + el("PAYMENTFAVOURING", bank.get("favouring"))
                     + el("BANKERSDATE", tx.tally_date(bank.get("bank_date")))
                     + el("PAYMENTMODE", "Transacted") + el("AMOUNT", fmt(value))
                     + "</BANKALLOCATIONS.LIST>")
    parts.append(extra_xml(entry.get("fields")))
    return f"<{tag}>" + "".join(parts) + f"</{tag}>"


def check_balanced(entries: list[dict[str, Any]]) -> None:
    total = sum((_signed(e) for e in entries), Decimal(0))
    if total != 0:
        debit = sum((-_signed(e) for e in entries if _signed(e) < 0), Decimal(0))
        credit = sum((_signed(e) for e in entries if _signed(e) > 0), Decimal(0))
        raise TallyError(f"Voucher does not balance: debits {fmt(debit)} vs credits {fmt(credit)} "
                         f"(difference {fmt(abs(total))}).")


def voucher_header(voucher_type: str, date: dt.date, *, number: str | None, narration: str | None,
                   party: str | None, reference: str | None, reference_date: Any = None,
                   optional: bool = False) -> str:
    stamp = tx.tally_date(date)
    return (el("DATE", stamp) + el("EFFECTIVEDATE", stamp) + el("VOUCHERTYPENAME", voucher_type)
            + el("VOUCHERNUMBER", number) + el("REFERENCE", reference)
            + el("REFERENCEDATE", tx.tally_date(reference_date)) + el("PARTYLEDGERNAME", party)
            + el("NARRATION", narration) + (el("ISOPTIONAL", True) if optional else ""))


def accounting_voucher_xml(voucher_type: str, date: Any, entries: list[dict[str, Any]], *,
                           number: str | None = None, narration: str | None = None,
                           party: str | None = None, reference: str | None = None,
                           reference_date: Any = None, optional: bool = False,
                           fields: dict[str, Any] | None = None) -> str:
    """Payment, Receipt, Contra, Journal - any voucher made only of ledger lines."""
    when = tx.parse_date(date)
    if not when:
        raise TallyError("A voucher date is required.")
    if len(entries) < 2:
        raise TallyError("A voucher needs at least two entries (one debit, one credit).")
    check_balanced(entries)
    body = voucher_header(voucher_type, when, number=number, narration=narration, party=party,
                          reference=reference, reference_date=reference_date, optional=optional)
    body += extra_xml(fields)
    body += "".join(ledger_entry_xml(e, when, is_party=bool(party) and e.get("ledger") == party)
                    for e in entries)
    return (f"<VOUCHER VCHTYPE={quoteattr(voucher_type)} ACTION=\"Create\" "
            f"OBJVIEW=\"Accounting Voucher View\">{body}</VOUCHER>")


def _qty_text(qty: Decimal, unit: str) -> str:
    return f" {qty.normalize():f} {unit}".rstrip()


def inventory_line_xml(tag: str, item: dict[str, Any], *, inward: bool, default_ledger: str | None,
                       default_godown: str) -> tuple[str, Decimal]:
    """One stock line. Returns the XML and the line's value (always positive)."""
    name = str(item.get("item") or item.get("name") or "").strip()
    if not name:
        raise TallyError("Every item line needs an item name.")
    qty, unit = dec(item.get("qty")), str(item.get("unit") or "").strip()
    price = dec(item.get("rate"))
    discount = dec(item.get("discount_percent"))
    if item.get("amount"):
        value = q2(dec(item["amount"]))
    else:
        value = q2(qty * price * (Decimal(100) - discount) / Decimal(100))
    if value <= 0:
        raise TallyError(f"Item '{name}' has no value. Give qty and rate, or amount.")
    signed = -value if inward else value
    qty_text = _qty_text(qty, unit) if qty else ""
    rate_text = f"{fmt(price)}/{unit}" if price and unit else (fmt(price) if price else "")
    parts = [el("STOCKITEMNAME", name), el("ISDEEMEDPOSITIVE", inward), el("RATE", rate_text),
             el("DISCOUNT", f"{discount.normalize():f}") if discount else "", el("AMOUNT", fmt(signed)),
             el("ACTUALQTY", qty_text), el("BILLEDQTY", qty_text)]
    if item.get("description"):
        parts.append("<BASICUSERDESCRIPTION.LIST TYPE=\"String\">" + el("BASICUSERDESCRIPTION", item["description"])
                     + "</BASICUSERDESCRIPTION.LIST>")
    parts.append("<BATCHALLOCATIONS.LIST>" + el("GODOWNNAME", item.get("godown") or default_godown)
                 + el("BATCHNAME", item.get("batch") or "Primary Batch") + el("AMOUNT", fmt(signed))
                 + el("ACTUALQTY", qty_text) + el("BILLEDQTY", qty_text) + "</BATCHALLOCATIONS.LIST>")
    ledger = item.get("ledger") or default_ledger
    if ledger:
        parts.append("<ACCOUNTINGALLOCATIONS.LIST>" + el("LEDGERNAME", ledger) + el("ISDEEMEDPOSITIVE", inward)
                     + el("AMOUNT", fmt(signed)) + "</ACCOUNTINGALLOCATIONS.LIST>")
    return f"<{tag}>" + "".join(parts) + f"</{tag}>", value


def invoice_xml(kind: str, date: Any, party: str, items: list[dict[str, Any]], *, ledger: str,
                number: str | None = None, reference: str | None = None, reference_date: Any = None,
                narration: str | None = None, voucher_type: str | None = None,
                taxes: list[dict[str, Any]] | None = None, gst: dict[str, Any] | None = None,
                charges: list[dict[str, Any]] | None = None, round_off_ledger: str | None = None,
                bill_name: str | None = None, bill_type: str | None = None,
                credit_days: int | None = None, place_of_supply: str | None = None,
                godown: str = "Main Location", fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """An item invoice (sales / purchase / credit note / debit note) with GST.

    Tax can be given two ways:
      * ``taxes``  - explicit ledger + amount lines, or
      * ``gst``    - {"cgst_ledger", "sgst_ledger", "igst_ledger", "interstate"}; tax is then worked out
                     from each item's ``gst_rate``.
    Returns {"xml", "summary"} so the caller can show the totals before posting.
    """
    key = kind.strip().lower().replace(" ", "_")
    if key not in INVOICE_KINDS:
        raise TallyError(f"kind must be one of: {', '.join(INVOICE_KINDS)}.")
    default_type, outward = INVOICE_KINDS[key]
    inward = not outward
    vch_type = voucher_type or default_type
    when = tx.parse_date(date)
    if not when:
        raise TallyError("An invoice date is required.")
    if not items:
        raise TallyError("An invoice needs at least one item line.")

    lines_xml, taxable, by_rate = "", Decimal(0), {}
    for item in items:
        xml, value = inventory_line_xml("ALLINVENTORYENTRIES.LIST", item, inward=inward,
                                        default_ledger=ledger, default_godown=godown)
        lines_xml += xml
        taxable += value
        if item.get("gst_rate"):
            by_rate[dec(item["gst_rate"])] = by_rate.get(dec(item["gst_rate"]), Decimal(0)) + value

    other: list[tuple[str, Decimal]] = []  # (ledger, positive amount) on the same side as sales/purchase
    for charge in charges or []:
        other.append((str(charge["ledger"]), q2(dec(charge["amount"]))))
    tax_lines: list[tuple[str, Decimal]] = [(str(t["ledger"]), q2(dec(t["amount"]))) for t in taxes or []]
    if gst and by_rate and not taxes:
        interstate = bool(gst.get("interstate"))
        igst = sum((q2(value * pct / 100) for pct, value in by_rate.items()), Decimal(0))
        half = sum((q2(value * pct / 200) for pct, value in by_rate.items()), Decimal(0))
        if interstate:
            if not gst.get("igst_ledger"):
                raise TallyError("gst.igst_ledger is required for an interstate invoice.")
            tax_lines.append((gst["igst_ledger"], igst))
        else:
            if not gst.get("cgst_ledger") or not gst.get("sgst_ledger"):
                raise TallyError("gst.cgst_ledger and gst.sgst_ledger are required for a local invoice.")
            tax_lines += [(gst["cgst_ledger"], half), (gst["sgst_ledger"], half)]
    tax_total = sum((a for _, a in tax_lines), Decimal(0))
    total = taxable + tax_total + sum((a for _, a in other), Decimal(0))
    round_off = Decimal(0)
    if round_off_ledger:
        rounded = total.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        round_off, total = rounded - total, rounded

    side = Decimal(-1) if inward else Decimal(1)  # sign of the sales / purchase side
    party_entry = {"ledger": party, "_signed": q2(-side * total)}
    ref = bill_name or reference or number
    if ref:
        party_entry["bills"] = [{"name": ref, "type": bill_type or "New Ref", "due_days": credit_days}]
    body = voucher_header(vch_type, when, number=number, narration=narration, party=party,
                          reference=reference, reference_date=reference_date)
    body += (el("PARTYNAME", party) + el("BASICBUYERNAME", party) + el("PLACEOFSUPPLY", place_of_supply)
             + el("PERSISTEDVIEW", "Invoice Voucher View") + el("ISINVOICE", True) + extra_xml(fields))
    body += ledger_entry_xml(party_entry, when, tag="LEDGERENTRIES.LIST", is_party=True)
    for name, value in tax_lines + other:
        if value:
            body += ledger_entry_xml({"ledger": name, "_signed": q2(side * value)}, when,
                                     tag="LEDGERENTRIES.LIST")
    if round_off:
        body += ledger_entry_xml({"ledger": round_off_ledger, "_signed": q2(side * round_off)}, when,
                                 tag="LEDGERENTRIES.LIST")
    body += lines_xml
    xml = (f"<VOUCHER VCHTYPE={quoteattr(vch_type)} ACTION=\"Create\" "
           f"OBJVIEW=\"Invoice Voucher View\">{body}</VOUCHER>")
    summary = {"voucher_type": vch_type, "date": when.isoformat(), "party": party,
               "taxable_value": tx.money(taxable), "tax": tx.money(tax_total),
               "tax_lines": [{"ledger": n, "amount": tx.money(a)} for n, a in tax_lines],
               "other_charges": tx.money(sum((a for _, a in other), Decimal(0))),
               "round_off": tx.money(round_off), "invoice_total": tx.money(total)}
    return {"xml": xml, "summary": summary}


def stock_journal_xml(date: Any, consumed: list[dict[str, Any]], produced: list[dict[str, Any]], *,
                      number: str | None = None, narration: str | None = None,
                      voucher_type: str = "Stock Journal", godown: str = "Main Location",
                      fields: dict[str, Any] | None = None) -> str:
    """Stock journal: items going out (consumed / source) and items coming in (produced / destination).

    Also the way to move stock between godowns: same item out of one godown, into another.
    """
    when = tx.parse_date(date)
    if not when:
        raise TallyError("A voucher date is required.")
    if not consumed and not produced:
        raise TallyError("Give at least one consumed or produced item line.")
    body = voucher_header(voucher_type, when, number=number, narration=narration, party=None, reference=None)
    body += extra_xml(fields)
    for item in consumed:
        body += inventory_line_xml("INVENTORYENTRIESOUT.LIST", item, inward=False, default_ledger=None,
                                   default_godown=godown)[0]
    for item in produced:
        body += inventory_line_xml("INVENTORYENTRIESIN.LIST", item, inward=True, default_ledger=None,
                                   default_godown=godown)[0]
    return (f"<VOUCHER VCHTYPE={quoteattr(voucher_type)} ACTION=\"Create\" "
            f"OBJVIEW=\"Consumption Voucher View\">{body}</VOUCHER>")


def voucher_action_xml(action: str, *, voucher_type: str, date: str, master_id: str, guid: str,
                       narration: str | None = None) -> str:
    """Delete or cancel one existing voucher, addressed by GUID and master id."""
    when = tx.parse_date(date)
    shown = when.strftime("%d-%b-%Y") if when else ""
    attrs = (f"REMOTEID={quoteattr(guid)} " if guid else "") + (
        f"DATE={quoteattr(shown)} TAGNAME=\"MASTERID\" TAGVALUE={quoteattr(str(master_id))} "
        if master_id else "")
    return (f"<VOUCHER {attrs}VCHTYPE={quoteattr(voucher_type)} ACTION={quoteattr(action)}>"
            + el("NARRATION", narration) + "</VOUCHER>")
