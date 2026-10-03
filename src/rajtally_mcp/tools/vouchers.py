"""Vouchers: read the day book, and create / alter / cancel / delete entries."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .. import builders, core
from .. import tally_xml as tx
from ..tally_xml import TallyError
from . import DELETE, READ, WRITE


def send_vouchers(xml: str, dry_run: bool, company: str | None, summary: dict[str, Any] | None = None
                  ) -> dict[str, Any]:
    core.require_write()
    if dry_run:
        out: dict[str, Any] = {"dry_run": True, "nothing_changed": True}
        if summary:
            out["summary"] = summary
        out["xml"] = xml
        return out
    result = tx.import_data(xml, "Vouchers", company)
    if summary:
        result["summary"] = summary
    if result.get("last_voucher_id"):
        result["master_id"] = result["last_voucher_id"]
    return result


def _full_voucher_xml(found: ET.Element, company: str | None) -> ET.Element:
    """The complete, import-ready XML of a voucher (taken from Tally's Day Book export)."""
    date = tx.text_of(found, "DATE")
    guid = tx.text_of(found, "GUID") or found.get("REMOTEID", "")
    master_id = tx.text_of(found, "MASTERID")
    try:
        root = tx.native_report("Day Book", company=company, from_date=date, to_date=date)
        for element in root.iter("VOUCHER"):
            if guid and (tx.text_of(element, "GUID") or element.get("REMOTEID", "")) == guid:
                return element
            if master_id and tx.text_of(element, "MASTERID") == master_id:
                return element
    except TallyError:
        pass
    return found


def _identify(found: ET.Element) -> dict[str, str]:
    return {"voucher_type": tx.text_of(found, "VOUCHERTYPENAME") or found.get("VCHTYPE", ""),
            "date": tx.text_of(found, "DATE"), "master_id": tx.text_of(found, "MASTERID"),
            "guid": tx.text_of(found, "GUID") or found.get("REMOTEID", "")}


def register(mcp: Any) -> None:

    @mcp.tool(annotations=READ)
    def tally_list_vouchers(
        from_date: str | None = None,
        to_date: str | None = None,
        voucher_type: str | None = None,
        ledger: str | None = None,
        party: str | None = None,
        search: str | None = None,
        min_amount: float | None = None,
        include_entries: bool = False,
        limit: int = 100,
        offset: int = 0,
        source: str = "auto",
        company: str | None = None,
    ) -> dict[str, Any]:
        """The day book: vouchers for a period, oldest first.

        Dates: YYYY-MM-DD, or leave blank for the current financial year, or from_date='FY2024-25'.
        voucher_type: Sales, Purchase, Payment, Receipt, Contra, Journal, Credit Note, Debit Note,
        Stock Journal, Payroll ... or any custom type name.
        ledger: only vouchers touching this ledger. party: only this party.
        search: text in narration / number / reference. min_amount: only vouchers of at least this value.
        include_entries=true adds the ledger lines and item lines.
        source: 'auto', 'collection' (fast) or 'daybook' (Tally's full Day Book export)."""
        start, end = tx.period(from_date, to_date)
        rows = core.fetch_vouchers(start, end, voucher_type=voucher_type, company=company, source=source)
        if ledger:
            want = ledger.strip().lower()
            rows = [v for v in rows if any(e["ledger"].lower() == want for e in v["entries"])]
        if party:
            want = party.strip().lower()
            rows = [v for v in rows if v["party"].lower() == want]
        if search:
            want = search.strip().lower()
            rows = [v for v in rows if want in (v["narration"] + " " + v["number"] + " " + v["reference"]).lower()]
        if min_amount:
            rows = [v for v in rows if v["amount"] >= float(min_amount)]
        total_value = tx.money(sum(v["amount"] for v in rows if not v["cancelled"]))
        if not include_entries:
            for voucher in rows:
                voucher.pop("entries", None)
                voucher.pop("items", None)
        result = core.page(rows, limit, offset, "vouchers")
        return {"from_date": start.isoformat(), "to_date": end.isoformat(), "total_value": total_value, **result}

    @mcp.tool(annotations=READ)
    def tally_get_voucher(master_id: str | None = None, guid: str | None = None, number: str | None = None,
                          voucher_type: str | None = None, date: str | None = None,
                          all_fields: bool = False, company: str | None = None) -> dict[str, Any]:
        """One voucher in full: ledger lines, bill references, cost centres, bank details, item lines.
        Identify it by master_id (from tally_list_vouchers), or guid, or number + voucher_type (+ date).
        all_fields=true also returns every raw Tally field."""
        found = core.find_voucher(master_id=master_id, guid=guid, number=number, voucher_type=voucher_type,
                                  date=date, company=company)
        voucher = core.parse_voucher(found)
        if all_fields:
            voucher["all_fields"] = tx.to_dict(_full_voucher_xml(found, company))
        return voucher

    @mcp.tool(annotations=WRITE)
    def tally_create_voucher(
        voucher_type: str,
        date: str,
        entries: list[dict[str, Any]],
        narration: str | None = None,
        number: str | None = None,
        party: str | None = None,
        reference: str | None = None,
        reference_date: str | None = None,
        optional: bool = False,
        fields: dict[str, Any] | None = None,
        dry_run: bool = False,
        company: str | None = None,
    ) -> dict[str, Any]:
        """Create an accounting voucher: Payment, Receipt, Contra, Journal, or a Sales / Purchase /
        Credit Note / Debit Note without stock items (service invoices).

        entries: two or more lines; debits must equal credits. Each line:
          {"ledger": "Rent", "dr": 15000}
          {"ledger": "HDFC Bank", "cr": 15000,
           "bank": {"transaction_type": "Cheque", "instrument_number": "000123",
                    "instrument_date": "2024-04-05", "favouring": "Landlord"}}
        Optional per line:
          "bills": [{"name": "INV-12", "type": "Agst Ref", "amount": 15000}]   type: New Ref, Agst Ref,
                                                                               Advance, On Account
          "cost_centres": [{"name": "Head Office", "amount": 15000, "category": "Primary Cost Category"}]
        number: leave blank for automatic numbering. optional=true saves it as an optional voucher.
        Examples -
          Payment: Dr expense / party, Cr cash or bank.     Receipt: Dr cash or bank, Cr party / income.
          Contra:  Dr one cash/bank ledger, Cr another.     Journal: any Dr / Cr adjustment."""
        xml = builders.accounting_voucher_xml(
            voucher_type, date, entries, number=number, narration=narration, party=party,
            reference=reference, reference_date=reference_date, optional=optional, fields=fields)
        debit = sum(float(e.get("dr") or 0) or (float(e.get("amount") or 0)
                    if str(e.get("type", "")).lower().startswith("d") else 0) for e in entries)
        summary = {"voucher_type": voucher_type, "date": tx.parse_date(date).isoformat(),
                   "amount": tx.money(debit), "lines": len(entries)}
        return send_vouchers(xml, dry_run, company, summary)

    @mcp.tool(annotations=WRITE)
    def tally_create_invoice(
        kind: str,
        date: str,
        party: str,
        items: list[dict[str, Any]],
        ledger: str,
        gst: dict[str, Any] | None = None,
        taxes: list[dict[str, Any]] | None = None,
        charges: list[dict[str, Any]] | None = None,
        round_off_ledger: str | None = None,
        number: str | None = None,
        reference: str | None = None,
        reference_date: str | None = None,
        narration: str | None = None,
        place_of_supply: str | None = None,
        credit_days: int | None = None,
        bill_name: str | None = None,
        bill_type: str | None = None,
        godown: str = "Main Location",
        voucher_type: str | None = None,
        fields: dict[str, Any] | None = None,
        dry_run: bool = False,
        company: str | None = None,
    ) -> dict[str, Any]:
        """Create an item invoice with stock and GST.

        kind: sales, purchase, credit_note (sales return) or debit_note (purchase return).
        ledger: the sales or purchase ledger, e.g. 'Sales @ 18%'.
        items: [{"item": "Ball Valve 2in", "qty": 10, "unit": "Nos", "rate": 450, "gst_rate": 18,
                 "discount_percent": 5, "godown": "Main Location"}]   (amount may replace qty x rate)
        Tax, one of:
          gst   = {"cgst_ledger": "CGST", "sgst_ledger": "SGST", "igst_ledger": "IGST", "interstate": false}
                  -> tax is calculated from each item's gst_rate
          taxes = [{"ledger": "CGST", "amount": 405}, {"ledger": "SGST", "amount": 405}]
        charges: extra ledgers on the invoice, e.g. [{"ledger": "Freight", "amount": 500}].
        round_off_ledger: rounds the total to the nearest rupee through this ledger.
        reference: the supplier's bill number for purchases. The returned summary shows taxable value,
        tax and invoice total - check it with dry_run=true before posting."""
        built = builders.invoice_xml(
            kind, date, party, items, ledger=ledger, number=number, reference=reference,
            reference_date=reference_date, narration=narration, voucher_type=voucher_type, taxes=taxes,
            gst=gst, charges=charges, round_off_ledger=round_off_ledger, bill_name=bill_name,
            bill_type=bill_type, credit_days=credit_days, place_of_supply=place_of_supply, godown=godown,
            fields=fields)
        return send_vouchers(built["xml"], dry_run, company, built["summary"])

    @mcp.tool(annotations=WRITE)
    def tally_create_stock_journal(
        date: str,
        consumed: list[dict[str, Any]] | None = None,
        produced: list[dict[str, Any]] | None = None,
        narration: str | None = None,
        number: str | None = None,
        voucher_type: str = "Stock Journal",
        dry_run: bool = False,
        company: str | None = None,
    ) -> dict[str, Any]:
        """Stock journal: move, consume or produce stock without an accounting entry.

        consumed: items going OUT (source). produced: items coming IN (destination).
        Each line: {"item": "Steel Rod", "qty": 5, "unit": "Kg", "rate": 60, "godown": "Main Location"}.
        Godown transfer = the same item in consumed (from godown) and produced (to godown)."""
        xml = builders.stock_journal_xml(date, consumed or [], produced or [], number=number,
                                         narration=narration, voucher_type=voucher_type)
        summary = {"voucher_type": voucher_type, "consumed_lines": len(consumed or []),
                   "produced_lines": len(produced or [])}
        return send_vouchers(xml, dry_run, company, summary)

    @mcp.tool(annotations=WRITE)
    def tally_alter_voucher(
        master_id: str | None = None,
        guid: str | None = None,
        number: str | None = None,
        voucher_type: str | None = None,
        date: str | None = None,
        new_date: str | None = None,
        new_number: str | None = None,
        new_narration: str | None = None,
        new_reference: str | None = None,
        new_entries: list[dict[str, Any]] | None = None,
        new_fields: dict[str, Any] | None = None,
        dry_run: bool = False,
        company: str | None = None,
    ) -> dict[str, Any]:
        """Edit an existing voucher. Identify it like tally_get_voucher, then pass only what should change.

        new_entries replaces ALL ledger lines of an accounting voucher (same format as
        tally_create_voucher); item invoices keep their item lines, so change those by deleting and
        re-creating the invoice. new_fields sets any other top-level Tally tag.
        The voucher is re-read afterwards and returned so the change can be checked."""
        core.require_write()
        found = core.find_voucher(master_id=master_id, guid=guid, number=number, voucher_type=voucher_type,
                                  date=date, company=company)
        ident = _identify(found)
        element = _full_voucher_xml(found, company)

        def set_tag(tag: str, value: str) -> None:
            node = element.find(tag)
            if node is None:
                node = ET.SubElement(element, tag)
            node.text = value

        when = tx.parse_date(new_date) if new_date else tx.parse_date(ident["date"])
        if new_date:
            set_tag("DATE", tx.tally_date(when))
            set_tag("EFFECTIVEDATE", tx.tally_date(when))
        if new_number is not None:
            set_tag("VOUCHERNUMBER", new_number)
        if new_narration is not None:
            set_tag("NARRATION", new_narration)
        if new_reference is not None:
            set_tag("REFERENCE", new_reference)
        for tag, value in (new_fields or {}).items():
            set_tag(tag.upper(), "Yes" if value is True else "No" if value is False else str(value))
        if new_entries is not None:
            if element.find("ALLINVENTORYENTRIES.LIST") is not None or element.find(
                    "INVENTORYENTRIES.LIST") is not None:
                raise TallyError("This voucher has item lines. Delete it and create it again with "
                                 "tally_create_invoice to change amounts.")
            builders.check_balanced(new_entries)
            for tag in ("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST"):
                for old in element.findall(tag):
                    element.remove(old)
            party = tx.text_of(element, "PARTYLEDGERNAME")
            for entry in new_entries:
                line = builders.ledger_entry_xml(entry, when, is_party=bool(party) and entry.get("ledger") == party)
                element.append(ET.fromstring(line))
        element.set("ACTION", "Alter")
        element.set("VCHTYPE", ident["voucher_type"])
        if ident["guid"]:
            element.set("REMOTEID", ident["guid"])
        xml = ET.tostring(element, encoding="unicode")
        if dry_run:
            return {"dry_run": True, "nothing_changed": True, "xml": xml}
        result = tx.import_data(xml, "Vouchers", company)
        try:
            after = core.find_voucher(guid=ident["guid"] or None,
                                      master_id=None if ident["guid"] else ident["master_id"],
                                      company=company)
            result["voucher_now"] = core.parse_voucher(after)
        except TallyError as exc:
            result["voucher_now"] = f"Could not re-read the voucher: {exc}"
        return result

    def remove(action: str, reason: str | None, confirm: bool, dry_run: bool, company: str | None,
               **identity: Any) -> dict[str, Any]:
        found = core.find_voucher(company=company, **identity)
        ident = _identify(found)
        before = core.parse_voucher(found, include_entries=False)
        xml = builders.voucher_action_xml(action, narration=reason, **ident)
        if dry_run:
            return {"dry_run": True, "nothing_changed": True, "voucher": before, "xml": xml}
        core.require_full(confirm)
        result = tx.import_data(xml, "Vouchers", company)
        counter = "deleted" if action == "Delete" else "cancelled"
        if not result.get(counter) and not result.get("altered"):
            result["ok"] = False
            result.setdefault("note", f"Tally did not report the voucher as {counter}.")
        result["voucher"] = before
        return result

    @mcp.tool(annotations=DELETE)
    def tally_cancel_voucher(master_id: str | None = None, guid: str | None = None,
                             number: str | None = None, voucher_type: str | None = None,
                             date: str | None = None, reason: str | None = None, confirm: bool = False,
                             dry_run: bool = False, company: str | None = None) -> dict[str, Any]:
        """Cancel a voucher: the number stays in the books, marked cancelled, with no financial effect.
        Preferred over deleting for invoices already issued. Needs access = 'full' and confirm=true."""
        return remove("Cancel", reason, confirm, dry_run, company, master_id=master_id, guid=guid,
                      number=number, voucher_type=voucher_type, date=date)

    @mcp.tool(annotations=DELETE)
    def tally_delete_voucher(master_id: str | None = None, guid: str | None = None,
                             number: str | None = None, voucher_type: str | None = None,
                             date: str | None = None, confirm: bool = False, dry_run: bool = False,
                             company: str | None = None) -> dict[str, Any]:
        """Permanently delete a voucher. Cannot be undone. Needs access = 'full' and confirm=true."""
        return remove("Delete", None, confirm, dry_run, company, master_id=master_id, guid=guid,
                      number=number, voucher_type=voucher_type, date=date)

    @mcp.tool(annotations=WRITE)
    def tally_bulk_import(
        vouchers: list[dict[str, Any]] | None = None,
        ledgers: list[dict[str, Any]] | None = None,
        stock_items: list[dict[str, Any]] | None = None,
        stop_on_error: bool = False,
        dry_run: bool = False,
        company: str | None = None,
    ) -> dict[str, Any]:
        """Post many records in one go, e.g. from a bank statement or an Excel sheet.

        vouchers:    each item takes the same arguments as tally_create_voucher
                     (voucher_type, date, entries, narration, number, party, reference).
        ledgers:     each item takes the same arguments as tally_create_ledger (name, group, gstin ...).
        stock_items: each item takes the same arguments as tally_create_stock_item (name, unit ...).
        Masters are posted first so vouchers can use them. Each record is posted on its own, so one bad
        row does not block the rest; the result lists every failure with its row number."""
        core.require_write()
        report: dict[str, Any] = {"posted": 0, "failed": 0, "failures": []}
        preview: list[str] = []

        def run(label: str, rows: list[dict[str, Any]] | None, build: Any, kind: str) -> bool:
            for index, row in enumerate(rows or [], start=1):
                try:
                    xml = build(dict(row))
                    if dry_run:
                        preview.append(xml)
                        report["posted"] += 1
                        continue
                    result = tx.import_data(xml, kind, company)
                    if result.get("ok"):
                        report["posted"] += 1
                    else:
                        raise TallyError("; ".join(result.get("line_errors", [])) or result.get("note", "rejected"))
                except (TallyError, KeyError, TypeError, ValueError) as exc:
                    report["failed"] += 1
                    report["failures"].append({"record": f"{label} #{index}", "problem": str(exc)})
                    if stop_on_error:
                        return False
            return True

        def ledger_xml(row: dict[str, Any]) -> str:
            name, alias = row.pop("name"), row.pop("alias", None)
            row["parent"] = row.pop("group", row.pop("parent", None))
            if row.get("address") or row.get("state") or row.get("gstin"):
                row.setdefault("mailing_name", name)
            row.setdefault("applicable_from", core.books_from(company))
            return builders.master_xml("LEDGER", name, builders.ledger_body(**row), alias=alias)

        def item_xml(row: dict[str, Any]) -> str:
            name, alias = row.pop("name"), row.pop("alias", None)
            row["parent"] = row.pop("stock_group", row.pop("parent", None))
            row.setdefault("applicable_from", core.books_from(company))
            return builders.master_xml("STOCKITEM", name, builders.stock_item_body(**row), alias=alias)

        def voucher_xml(row: dict[str, Any]) -> str:
            return builders.accounting_voucher_xml(row.pop("voucher_type"), row.pop("date"),
                                                   row.pop("entries"), **row)

        if run("ledger", ledgers, ledger_xml, "All Masters") and run("stock_item", stock_items, item_xml,
                                                                    "All Masters"):
            run("voucher", vouchers, voucher_xml, "Vouchers")
        if dry_run:
            report = {"dry_run": True, "nothing_changed": True, "would_post": report["posted"],
                      "failed": report["failed"], "failures": report["failures"],
                      "xml_preview": preview[:3]}
        return report

    @mcp.tool(annotations=WRITE)
    def tally_import_xml(xml: str, kind: str = "vouchers", dry_run: bool = False,
                         company: str | None = None) -> dict[str, Any]:
        """Import ready-made Tally XML objects (the content that goes inside <TALLYMESSAGE>): one or more
        <VOUCHER ...> or master elements such as <LEDGER ...>. For anything the other write tools do not
        cover - sales / purchase orders, delivery notes, payroll vouchers, price lists, budgets.
        kind: 'vouchers' or 'masters'."""
        core.require_write()
        target = "Vouchers" if kind.strip().lower().startswith("v") else "All Masters"
        text = xml.strip()
        if "<ENVELOPE" in text.upper():
            raise TallyError("Pass only the objects (e.g. <VOUCHER>...</VOUCHER>), not a full <ENVELOPE>. "
                             "To send a complete envelope use tally_raw_xml.")
        if 'ACTION="Delete"' in text or "ACTION='Delete'" in text or 'ACTION="Cancel"' in text:
            core.require_full(True)
        if dry_run:
            return {"dry_run": True, "nothing_changed": True, "xml": tx.import_xml(text, target, company)}
        return tx.import_data(text, target, company)

