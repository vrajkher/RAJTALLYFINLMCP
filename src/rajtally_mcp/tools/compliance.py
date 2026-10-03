"""Compliance and review: TDS, bank reconciliation, audit checks, master-data health."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any

from .. import core
from .. import tally_xml as tx
from ..tally_xml import TallyError
from . import READ
from .gst import base_types, check_gstin, tax_head
from .reports import books


def _is_cash(ledger: dict[str, Any]) -> bool:
    return "cash-in-hand" in ledger["chain"]


def _is_party(ledger: dict[str, Any]) -> bool:
    return "sundry debtors" in ledger["chain"] or "sundry creditors" in ledger["chain"]


def register(mcp: Any) -> None:

    @mcp.tool(annotations=READ)
    def tally_tds_summary(from_date: str | None = None, to_date: str | None = None,
                          company: str | None = None) -> dict[str, Any]:
        """TDS for a period, from the books: tax deducted and tax deposited for each TDS ledger
        (section), the balance still payable, and deductions party by party with PAN - the working
        data for the quarterly TDS return (26Q / 27Q). Flags deductees with no PAN."""
        start, end = tx.period(from_date, to_date)
        data = books(start, end, company)
        ledgers = {l["name"].lower(): l for l in data["ledgers"]}
        tds = {name for name, l in ledgers.items() if tax_head(l) == "tds"}
        if not tds:
            return {"from_date": start.isoformat(), "to_date": end.isoformat(), "tds_ledgers": [],
                    "note": "No TDS ledgers found under Duties & Taxes (looked for tax type TDS or 'TDS' "
                            "in the ledger name)."}
        by_ledger: dict[str, dict[str, Decimal]] = {}
        by_party: dict[tuple[str, str], dict[str, Any]] = {}
        for voucher in core.fetch_vouchers(start, end, company=company):
            if voucher["cancelled"] or voucher["optional"]:
                continue
            lines = [e for e in voucher["entries"] if e["ledger"].lower() in tds]
            if not lines:
                continue
            party = voucher["party"] or next(
                (e["ledger"] for e in voucher["entries"]
                 if e["ledger"].lower() in ledgers and _is_party(ledgers[e["ledger"].lower()])), "")
            for line in lines:
                row = by_ledger.setdefault(line["ledger"], {"deducted": Decimal(0), "deposited": Decimal(0)})
                row["deducted"] += Decimal(str(line["cr"]))
                row["deposited"] += Decimal(str(line["dr"]))
                if line["cr"] and party:
                    entry = by_party.setdefault((party, line["ledger"]), {
                        "party": party, "tds_ledger": line["ledger"],
                        "pan": ledgers.get(party.lower(), {}).get("pan", ""), "deducted": Decimal(0),
                        "vouchers": 0})
                    entry["deducted"] += Decimal(str(line["cr"]))
                    entry["vouchers"] += 1
        ledger_rows = []
        for name in sorted({ledgers[n]["name"] for n in tds}):
            moved = by_ledger.get(name, {"deducted": Decimal(0), "deposited": Decimal(0)})
            closing = ledgers[name.lower()]["closing"]
            ledger_rows.append({"tds_ledger": name, "deducted": tx.money(moved["deducted"]),
                                "deposited": tx.money(moved["deposited"]),
                                "balance_payable": tx.money(closing)})
        party_rows = sorted(({**p, "deducted": tx.money(p["deducted"])} for p in by_party.values()),
                            key=lambda r: -r["deducted"])
        return {"from_date": start.isoformat(), "to_date": end.isoformat(),
                "total_deducted": tx.money(sum(r["deducted"] for r in by_ledger.values())),
                "total_deposited": tx.money(sum(r["deposited"] for r in by_ledger.values())),
                "tds_ledgers": ledger_rows, "deductees": party_rows,
                "deductees_without_pan": sorted({p["party"] for p in party_rows if not p["pan"]})}

    @mcp.tool(annotations=READ)
    def tally_bank_reconciliation(bank_ledger: str, from_date: str | None = None, to_date: str | None = None,
                                  company: str | None = None) -> dict[str, Any]:
        """Bank reconciliation statement for one bank ledger: balance as per books, cheques issued but
        not yet presented, deposits not yet credited, and the resulting balance as per bank.
        An entry counts as cleared when it has a bank date on or before to_date.
        Start from_date early enough to include old unpresented cheques."""
        start, end = tx.period(from_date, to_date)
        data = books(start, end, company)
        match = [l for l in data["ledgers"] if l["name"].lower() == bank_ledger.strip().lower()]
        if not match:
            raise TallyError(f"No ledger named '{bank_ledger}'. Use tally_search to find the exact name.")
        ledger = match[0]
        name = ledger["name"]
        pending: list[dict[str, Any]] = []
        cleared = 0
        for voucher in core.fetch_vouchers(start, end, company=company):
            if voucher["cancelled"] or voucher["optional"]:
                continue
            for entry in voucher["entries"]:
                if entry["ledger"].lower() != name.lower():
                    continue
                details = entry.get("bank") or [{}]
                for detail in details:
                    bank_date = detail.get("bank_date", "")
                    if bank_date and bank_date <= end.isoformat():
                        cleared += 1
                        continue
                    amount = Decimal(str(detail.get("amount") or 0)) or Decimal(str(entry["dr"] or entry["cr"]))
                    pending.append({
                        "date": voucher["date"], "type": voucher["type"], "number": voucher["number"],
                        "party": voucher["party"], "instrument_number": detail.get("instrument_number", ""),
                        "instrument_date": detail.get("instrument_date", ""),
                        "kind": "deposit not credited" if entry["dr"] else "payment not presented",
                        "amount": tx.money(amount), "master_id": voucher["master_id"]})
        books_balance = -ledger["closing"]  # positive = money in the bank
        deposits = sum(Decimal(str(p["amount"])) for p in pending if p["kind"] == "deposit not credited")
        payments = sum(Decimal(str(p["amount"])) for p in pending if p["kind"] == "payment not presented")
        return {"bank_ledger": name, "as_on": end.isoformat(), "entries_from": start.isoformat(),
                "balance_as_per_books": tx.money(books_balance),
                "add_payments_not_presented": tx.money(payments),
                "less_deposits_not_credited": tx.money(deposits),
                "balance_as_per_bank": tx.money(books_balance + payments - deposits),
                "cleared_entries": cleared, "uncleared_count": len(pending), "uncleared": pending[:1000],
                "note": "Negative balance = overdrawn."}

    @mcp.tool(annotations=READ)
    def tally_audit_checks(from_date: str | None = None, to_date: str | None = None,
                           cash_payment_limit: float = 10000, cash_receipt_limit: float = 200000,
                           company: str | None = None) -> dict[str, Any]:
        """Audit review of a period. Scans every voucher and ledger and reports:
        cash payments above the Sec 40A(3) limit, cash receipts at or above the Sec 269ST limit,
        days the cash balance went negative, entries dated on Sundays, vouchers without narration,
        duplicate voucher numbers, a supplier bill booked twice, large round-sum journals, debtors with
        credit balances and creditors with debit balances, suspense balances, and the largest vouchers.
        Each finding lists the vouchers so they can be opened with tally_get_voucher."""
        start, end = tx.period(from_date, to_date)
        data = books(start, end, company)
        ledgers = {l["name"].lower(): l for l in data["ledgers"]}
        bases = base_types(company)
        vouchers = [v for v in core.fetch_vouchers(start, end, company=company)
                    if not v["cancelled"] and not v["optional"]]

        def brief(voucher: dict[str, Any], **extra: Any) -> dict[str, Any]:
            return {"date": voucher["date"], "type": voucher["type"], "number": voucher["number"],
                    "party": voucher["party"], "amount": voucher["amount"], "master_id": voucher["master_id"],
                    **extra}

        cash_paid, cash_received, sundays, no_narration, round_journals = [], [], [], [], []
        numbers: dict[tuple[str, str], list[dict[str, Any]]] = {}
        supplier_bills: dict[tuple[str, str], list[dict[str, Any]]] = {}
        cash_by_day: dict[str, Decimal] = {}
        for voucher in vouchers:
            base = bases.get(voucher["type"].lower(), voucher["type"].lower())
            cash_dr = sum(Decimal(str(e["dr"])) for e in voucher["entries"]
                          if e["ledger"].lower() in ledgers and _is_cash(ledgers[e["ledger"].lower()]))
            cash_cr = sum(Decimal(str(e["cr"])) for e in voucher["entries"]
                          if e["ledger"].lower() in ledgers and _is_cash(ledgers[e["ledger"].lower()]))
            if cash_dr or cash_cr:
                cash_by_day[voucher["date"]] = cash_by_day.get(voucher["date"], Decimal(0)) + cash_dr - cash_cr
            if base != "contra":
                if cash_cr > Decimal(str(cash_payment_limit)):
                    cash_paid.append(brief(voucher, cash_paid=tx.money(cash_cr)))
                if cash_dr >= Decimal(str(cash_receipt_limit)):
                    cash_received.append(brief(voucher, cash_received=tx.money(cash_dr)))
            when = tx.parse_date(voucher["date"])
            if when and when.weekday() == 6:
                sundays.append(brief(voucher))
            if not voucher["narration"]:
                no_narration.append(brief(voucher))
            if voucher["number"]:
                numbers.setdefault((voucher["type"], voucher["number"]), []).append(voucher)
            if base == "purchase" and voucher["reference"] and voucher["party"]:
                supplier_bills.setdefault((voucher["party"].lower(), voucher["reference"].strip().lower()),
                                          []).append(voucher)
            if base == "journal" and voucher["amount"] >= 100000 and voucher["amount"] % 1000 == 0:
                round_journals.append(brief(voucher))

        negative_cash = []
        running = sum((-l["opening"] for l in data["ledgers"] if _is_cash(l)), Decimal(0))
        for day in sorted(cash_by_day):
            running += cash_by_day[day]
            if running < 0:
                negative_cash.append({"date": day, "cash_balance": tx.money(running)})

        wrong_side = []
        suspense = []
        for ledger in data["ledgers"]:
            if "sundry debtors" in ledger["chain"] and ledger["closing"] > 0:
                wrong_side.append({"ledger": ledger["name"], "issue": "Debtor with credit balance",
                                   "balance": tx.money(ledger["closing"])})
            if "sundry creditors" in ledger["chain"] and ledger["closing"] < 0:
                wrong_side.append({"ledger": ledger["name"], "issue": "Creditor with debit balance",
                                   "balance": tx.money(-ledger["closing"])})
            if "suspense a/c" in ledger["chain"] and ledger["closing"]:
                balance = tx.dr_cr(ledger["closing"])
                suspense.append({"ledger": ledger["name"], "dr": balance["dr"], "cr": balance["cr"]})

        duplicate_numbers = [{"type": key[0], "number": key[1], "vouchers": [brief(v) for v in group]}
                             for key, group in numbers.items() if len(group) > 1]
        double_booked = [{"party": group[0]["party"], "supplier_bill": group[0]["reference"],
                          "vouchers": [brief(v) for v in group]}
                         for group in supplier_bills.values() if len(group) > 1]
        largest = [brief(v) for v in sorted(vouchers, key=lambda v: -v["amount"])[:10]]

        def section(title: str, rows: list[Any], why: str) -> dict[str, Any]:
            return {"check": title, "found": len(rows), "why_it_matters": why, "items": rows[:100]}

        findings = [
            section(f"Cash payments above Rs {cash_payment_limit:,.0f}", cash_paid,
                    "Sec 40A(3): expenditure paid in cash above the limit to a person in a day can be disallowed."),
            section(f"Cash receipts of Rs {cash_receipt_limit:,.0f} or more", cash_received,
                    "Sec 269ST: receiving Rs 2 lakh or more in cash attracts a penalty equal to the amount."),
            section("Days with negative cash balance", negative_cash,
                    "Cash cannot be negative; it points to unrecorded receipts or wrongly dated payments."),
            section("Vouchers dated on a Sunday", sundays, "Worth confirming the date if the business is closed."),
            section("Vouchers without narration", no_narration, "Narration is the audit trail for the entry."),
            section("Duplicate voucher numbers", duplicate_numbers, "The same number used twice within a voucher type."),
            section("Supplier bill booked more than once", double_booked,
                    "Same party and same supplier bill number - possible double booking and double ITC."),
            section("Large round-sum journals", round_journals,
                    "Journals of Rs 1 lakh or more in round thousands deserve supporting papers."),
            section("Debtors / creditors on the wrong side", wrong_side,
                    "Advances or mis-postings that should be regrouped or reconciled."),
            section("Suspense account balances", suspense, "Should be cleared before finalisation."),
        ]
        return {"from_date": start.isoformat(), "to_date": end.isoformat(), "vouchers_scanned": len(vouchers),
                "checks_with_findings": sum(1 for f in findings if f["found"]), "findings": findings,
                "largest_vouchers": largest,
                "note": "These are pointers for review, not conclusions. Thresholds can be changed with "
                        "cash_payment_limit and cash_receipt_limit."}

    @mcp.tool(annotations=READ)
    def tally_data_health(company: str | None = None) -> dict[str, Any]:
        """Master-data quality check: party ledgers with missing or invalid GSTIN, GSTIN state not
        matching the ledger's state, the same GSTIN on several ledgers, parties without PAN or state,
        look-alike duplicate ledger names, and stock items without HSN code, GST rate or unit."""
        today = dt.date.today()
        data = books(tx.financial_year(today)[0], today, company)
        parties = [l for l in data["ledgers"] if _is_party(l)]
        no_gstin, bad_gstin, state_mismatch, no_state, no_pan = [], [], [], [], []
        by_gstin: dict[str, list[str]] = {}
        for ledger in parties:
            if not ledger["gstin"]:
                no_gstin.append(ledger["name"])
            else:
                verdict = check_gstin(ledger["gstin"])
                by_gstin.setdefault(verdict["gstin"], []).append(ledger["name"])
                if not verdict["valid"]:
                    bad_gstin.append({"ledger": ledger["name"], "gstin": ledger["gstin"],
                                      "problem": verdict["problem"]})
                elif ledger["state"] and verdict["state"].lower() != ledger["state"].lower():
                    state_mismatch.append({"ledger": ledger["name"], "gstin_state": verdict["state"],
                                           "ledger_state": ledger["state"]})
            if not ledger["state"]:
                no_state.append(ledger["name"])
            if not ledger["pan"] and not ledger["gstin"]:
                no_pan.append(ledger["name"])
        shared = [{"gstin": g, "ledgers": names} for g, names in by_gstin.items() if len(names) > 1]
        alike: dict[str, list[str]] = {}
        for ledger in data["ledgers"]:
            alike.setdefault(re.sub(r"[^a-z0-9]", "", ledger["name"].lower()), []).append(ledger["name"])
        look_alike = [names for names in alike.values() if len(names) > 1]
        try:
            items = core.list_masters("stock_item", company=company)
        except TallyError:
            items = []
        return {
            "party_ledgers": len(parties), "stock_items": len(items),
            "parties_without_gstin": {"count": len(no_gstin), "ledgers": no_gstin[:200]},
            "invalid_gstin": bad_gstin,
            "gstin_state_differs_from_ledger_state": state_mismatch,
            "same_gstin_on_several_ledgers": shared,
            "parties_without_state": {"count": len(no_state), "ledgers": no_state[:200]},
            "parties_without_pan_or_gstin": {"count": len(no_pan), "ledgers": no_pan[:200]},
            "look_alike_ledger_names": look_alike,
            "items_without_hsn": [i["name"] for i in items if not i.get("hsn")][:200],
            "items_without_gst_rate": [i["name"] for i in items if i.get("gst_rate") is None][:200],
            "items_without_unit": [i["name"] for i in items if not i.get("unit")][:200],
        }
