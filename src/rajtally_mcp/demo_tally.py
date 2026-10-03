"""A small pretend Tally, for trying the MCP server (and running the tests) without Tally installed.

    rajtally-mcp --demo          # listens on http://localhost:9000 like Tally does

It speaks the same XML: collection exports with filters, the Day Book and Bills reports, TDL function
calls, and master / voucher imports (create, alter, delete, cancel). Data lives in memory and is reset
on every start. It is a teaching aid, not a replacement for testing against your own Tally.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
import threading
import xml.etree.ElementTree as ET
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from xml.sax.saxutils import escape

from . import builders
from . import tally_xml as tx

PRIMARY = "&#4; Primary"
MASTER_TAGS = {"Ledger": "LEDGER", "Group": "GROUP", "StockItem": "STOCKITEM", "StockGroup": "STOCKGROUP",
               "StockCategory": "STOCKCATEGORY", "Unit": "UNIT", "Godown": "GODOWN",
               "CostCentre": "COSTCENTRE", "CostCategory": "COSTCATEGORY", "VoucherType": "VOUCHERTYPE",
               "Currency": "CURRENCY", "Budget": "BUDGET", "AttendanceType": "ATTENDANCETYPE"}
ENTRY_TAGS = ("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST")
ITEM_TAGS = ("ALLINVENTORYENTRIES.LIST", "INVENTORYENTRIES.LIST", "INVENTORYENTRIESIN.LIST",
             "INVENTORYENTRIESOUT.LIST")
BASE_TESTS = {"issales": "Sales", "ispurchase": "Purchase", "ispayment": "Payment", "isreceipt": "Receipt",
              "iscontra": "Contra", "isjournal": "Journal", "iscreditnote": "Credit Note",
              "isdebitnote": "Debit Note"}


def gstin(prefix14: str) -> str:
    """Append the correct check character to the first 14 characters of a GSTIN."""
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    total = 0
    for index, char in enumerate(prefix14):
        product = chars.index(char) * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    return prefix14 + chars[(36 - total % 36) % 36]


class DemoTally:
    """In-memory company that answers Tally XML requests."""

    def __init__(self, seed: bool = True) -> None:
        self.company = "Demo Traders Pvt Ltd"
        self.fy_start = tx.financial_year()[0]
        self.masters: dict[str, dict[str, ET.Element]] = {tag: {} for tag in MASTER_TAGS.values()}
        self.vouchers: list[ET.Element] = []
        self.next_id = 1
        self.lock = threading.Lock()
        self.requests: list[str] = []  # every request received, for tests
        if seed:
            self._seed()
            self.requests.clear()

    # ---------------------------------------------------------------- data helpers
    def _ids(self, element: ET.Element) -> None:
        for tag, value in (("MASTERID", str(self.next_id)), ("ALTERID", str(self.next_id)),
                           ("GUID", f"demo-guid-{self.next_id:08d}")):
            node = element.find(tag)
            if node is None:
                node = ET.SubElement(element, tag)
            node.text = value
        self.next_id += 1

    def _find(self, tag: str, name: str) -> ET.Element | None:
        for key, element in self.masters[tag].items():
            if key.lower() == name.lower():
                return element
        return None

    def _voucher_ledgers(self, voucher: ET.Element) -> list[tuple[str, Decimal]]:
        rows = []
        for tag in ENTRY_TAGS:
            for entry in voucher.findall(tag):
                rows.append((tx.text_of(entry, "LEDGERNAME"), tx.amount(tx.text_of(entry, "AMOUNT"))))
        for tag in ITEM_TAGS:
            for line in voucher.findall(tag):
                for alloc in line.findall("ACCOUNTINGALLOCATIONS.LIST"):
                    rows.append((tx.text_of(alloc, "LEDGERNAME"), tx.amount(tx.text_of(alloc, "AMOUNT"))))
        return rows

    def _live(self, voucher: ET.Element) -> bool:
        return not tx.yes(tx.text_of(voucher, "ISCANCELLED")) and not tx.yes(tx.text_of(voucher, "ISOPTIONAL"))

    def _ledger_balance(self, name: str, upto: str | None, inclusive: bool) -> Decimal:
        master = self._find("LEDGER", name)
        total = tx.amount(tx.text_of(master, "OPENINGBALANCE")) if master is not None else Decimal(0)
        for voucher in self.vouchers:
            if not self._live(voucher):
                continue
            date = tx.text_of(voucher, "DATE")
            if not upto and not inclusive:
                continue  # opening balance with no period given = the master's opening balance
            if upto and (date > upto or (date == upto and not inclusive)):
                continue
            total += sum((a for n, a in self._voucher_ledgers(voucher) if n.lower() == name.lower()), Decimal(0))
        return total

    def _stock(self, name: str, upto: str | None) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """opening qty, opening value, closing qty, closing value (weighted average cost)."""
        master = self._find("STOCKITEM", name)
        open_qty = tx.quantity(tx.text_of(master, "OPENINGBALANCE"))[0]
        open_value = abs(tx.amount(tx.text_of(master, "OPENINGVALUE")))
        in_qty, in_value, out_qty = open_qty, open_value, Decimal(0)
        for voucher in self.vouchers:
            if not self._live(voucher) or (upto and tx.text_of(voucher, "DATE") > upto):
                continue
            for tag in ITEM_TAGS:
                for line in voucher.findall(tag):
                    if tx.text_of(line, "STOCKITEMNAME").lower() != name.lower():
                        continue
                    qty = tx.quantity(tx.text_of(line, "BILLEDQTY") or tx.text_of(line, "ACTUALQTY"))[0]
                    inward = tag.endswith("IN.LIST") or (not tag.endswith("OUT.LIST")
                                                         and tx.yes(tx.text_of(line, "ISDEEMEDPOSITIVE")))
                    if inward:
                        in_qty += qty
                        in_value += abs(tx.amount(tx.text_of(line, "AMOUNT")))
                    else:
                        out_qty += qty
        closing_qty = in_qty - out_qty
        average = in_value / in_qty if in_qty else Decimal(0)
        return open_qty, open_value, closing_qty, closing_qty * average

    # ---------------------------------------------------------------- export
    def _master_out(self, tag: str, element: ET.Element, start: str | None, end: str | None) -> ET.Element:
        out = copy.deepcopy(element)
        out.set("RESERVEDNAME", "")

        def put(name: str, value: str) -> None:
            node = out.find(name)
            if node is None:
                node = ET.SubElement(out, name)
            node.text = value

        name = element.get("NAME", "")
        if tag == "LEDGER":
            put("OPENINGBALANCE", f"{self._ledger_balance(name, start, False):.2f}")
            put("CLOSINGBALANCE", f"{self._ledger_balance(name, end, True):.2f}")
        elif tag == "STOCKITEM":
            unit = tx.text_of(element, "BASEUNITS")
            open_qty, open_value, close_qty, close_value = self._stock(name, end)
            put("OPENINGBALANCE", f" {open_qty.normalize():f} {unit}")
            put("OPENINGVALUE", f"{-open_value:.2f}")
            put("CLOSINGBALANCE", f" {close_qty.normalize():f} {unit}")
            put("CLOSINGVALUE", f"{-close_value:.2f}")
            put("CLOSINGRATE", f"{(close_value / close_qty if close_qty else Decimal(0)):.2f}/{unit}")
        return out

    def _under(self, tag: str, element: ET.Element, ancestor: str) -> bool:
        parent_tag = "GROUP" if tag in ("LEDGER", "GROUP") else "STOCKGROUP" if tag == "STOCKITEM" else tag
        parent, seen = tx.text_of(element, "PARENT"), set()
        while parent and parent not in seen:
            if parent.lower() == ancestor.lower():
                return True
            seen.add(parent)
            node = self._find(parent_tag, parent)
            parent = tx.text_of(node, "PARENT") if node is not None else ""
        return False

    def _value(self, element: ET.Element, field: str) -> str:
        if field.lower() == "name":
            return element.get("NAME") or tx.text_of(element, "NAME")
        return tx.text_of(element, field.upper())

    def _test(self, element: ET.Element, formula: str, start: str | None, end: str | None) -> bool:
        formula = formula.strip()
        for joiner, combine in ((" OR ", any), (" AND ", all)):
            parts = re.split(joiner, formula, flags=re.IGNORECASE)
            if len(parts) > 1:
                return combine(self._test(element, part, start, end) for part in parts)
        if formula.upper().startswith("NOT "):
            return not self._test(element, formula[4:], start, end)
        match = re.fullmatch(r"\$\$(\w+):\$(\w+)", formula)
        if match:
            function, value = match[1].lower(), self._value(element, match[2])
            if function == "isempty":
                return value == ""
            base = BASE_TESTS.get(function)
            if base:
                node = self._find("VOUCHERTYPE", value)
                while node is not None and tx.text_of(node, "PARENT") not in ("", node.get("NAME")):
                    value = tx.text_of(node, "PARENT")
                    node = self._find("VOUCHERTYPE", value)
                return value.lower() == base.lower()
            return True
        match = re.fullmatch(r"\$([\w.]+)\s*(>=|<=|<>|=|<|>|CONTAINS|STARTING WITH|ENDING WITH)\s*(.+)",
                             formula, flags=re.IGNORECASE)
        if match:
            left, operator, right = self._value(element, match[1]), match[2].upper(), match[3].strip()
            if right == "##SVFromDate":
                right = start or "00000000"
            elif right == "##SVToDate":
                right = end or "99999999"
            elif right == "##SVCurrentCompany":
                right = self.company
            right = right.strip('"')
            if operator in ("CONTAINS", "STARTING WITH", "ENDING WITH"):
                a, b = left.lower(), right.lower()
                return b in a if operator == "CONTAINS" else a.startswith(b) if operator == "STARTING WITH" \
                    else a.endswith(b)
            try:
                a, b = Decimal(left or "0"), Decimal(right)
            except ArithmeticError:
                a, b = left.lower(), right.lower()  # type: ignore[assignment]
            return {"=": a == b, "<>": a != b, ">=": a >= b, "<=": a <= b, "<": a < b, ">": a > b}[operator]
        match = re.fullmatch(r"\$(\w+)", formula)
        if match:
            return tx.yes(self._value(element, match[1]))
        return True

    def _collection(self, root: ET.Element, start: str | None, end: str | None) -> str:
        definition = root.find(".//COLLECTION")
        kind = tx.text_of(definition, "TYPE")
        formulae = {s.get("NAME"): s.text or "" for s in root.iter("SYSTEM")}
        tests = [formulae.get(f.text or "", "") for f in definition.findall("FILTER")]
        child_of = tx.text_of(definition, "CHILDOF")
        if kind == "Company":
            objects = [ET.fromstring(
                f'<COMPANY NAME="{escape(self.company)}"><STARTINGFROM>{self.fy_start:%Y%m%d}</STARTINGFROM>'
                f"<BOOKSFROM>{self.fy_start:%Y%m%d}</BOOKSFROM><STATENAME>Gujarat</STATENAME>"
                "<COUNTRYNAME>India</COUNTRYNAME><PINCODE>380001</PINCODE>"
                f"<GSTREGISTRATIONNUMBER>{gstin('24AAACD1234E1Z')}</GSTREGISTRATIONNUMBER>"
                "<INCOMETAXNUMBER>AAACD1234E</INCOMETAXNUMBER><ISINVENTORYON>Yes</ISINVENTORYON>"
                "<ISINTEGRATED>Yes</ISINTEGRATED><ISBILLWISEON>Yes</ISBILLWISEON><ISGSTON>Yes</ISGSTON>"
                "<ADDRESS.LIST><ADDRESS>12 Ashram Road</ADDRESS><ADDRESS>Ahmedabad</ADDRESS></ADDRESS.LIST>"
                "<RAJISACTIVE>Yes</RAJISACTIVE><GUID>demo-company</GUID></COMPANY>")]
        elif kind == "Voucher":
            objects = [self._voucher_out(v) for v in self.vouchers
                       if (not start or tx.text_of(v, "DATE") >= start)
                       and (not end or tx.text_of(v, "DATE") <= end)]
        elif kind == "Bills":
            objects = [ET.fromstring(
                f'<BILL NAME="{escape(b["bill"])}"><PARENT>{escape(b["party"])}</PARENT>'
                f'<BILLDATE>{b["date"]}</BILLDATE><CLOSINGBALANCE>{b["pending"]:.2f}</CLOSINGBALANCE></BILL>')
                for b in self._bills(end)]
        elif kind in MASTER_TAGS:
            tag = MASTER_TAGS[kind]
            objects = [self._master_out(tag, e, start, end) for e in self.masters[tag].values()
                       if not child_of or self._under(tag, e, child_of)]
        else:
            return self._error(f"Collection type '{kind}' is not available in the demo")
        objects = [o for o in objects if all(self._test(o, t, start, end) for t in tests)]
        body = "".join(ET.tostring(o, encoding="unicode") for o in objects).replace("&amp;#4;", "&#4;")
        return ("<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DESC><CMPINFO>"
                f"<LEDGER>{len(self.masters['LEDGER'])}</LEDGER></CMPINFO></DESC><DATA>"
                f'<COLLECTION ISMSTDEPTYPE="Yes" MSTDEPTYPE="1">{body}</COLLECTION></DATA></BODY></ENVELOPE>')

    def _voucher_out(self, voucher: ET.Element) -> ET.Element:
        out = copy.deepcopy(voucher)
        out.set("REMOTEID", tx.text_of(voucher, "GUID"))
        for attr in ("ACTION", "TAGNAME", "TAGVALUE", "DATE"):
            out.attrib.pop(attr, None)
        return out

    def _bills(self, end: str | None) -> list[dict[str, Any]]:
        bills: dict[tuple[str, str], dict[str, Any]] = {}
        for voucher in self.vouchers:
            if not self._live(voucher) or (end and tx.text_of(voucher, "DATE") > end):
                continue
            for tag in ENTRY_TAGS:
                for entry in voucher.findall(tag):
                    for bill in entry.findall("BILLALLOCATIONS.LIST"):
                        key = (tx.text_of(entry, "LEDGERNAME"), tx.text_of(bill, "NAME"))
                        row = bills.setdefault(key, {"party": key[0], "bill": key[1], "pending": Decimal(0),
                                                     "date": tx.text_of(voucher, "DATE")})
                        row["pending"] += tx.amount(tx.text_of(bill, "AMOUNT"))
        return [b for b in bills.values() if b["pending"]]

    def _report(self, name: str, start: str | None, end: str | None) -> str:
        if name.lower() == "day book":
            body = "".join(ET.tostring(self._voucher_out(v), encoding="unicode") for v in self.vouchers
                           if (not start or tx.text_of(v, "DATE") >= start)
                           and (not end or tx.text_of(v, "DATE") <= end))
            return ("<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA>"
                    "<REQUESTDESC><REPORTNAME>All Masters</REPORTNAME></REQUESTDESC><REQUESTDATA>"
                    f'<TALLYMESSAGE xmlns:UDF="TallyUDF">{body}</TALLYMESSAGE></REQUESTDATA></IMPORTDATA>'
                    "</BODY></ENVELOPE>")
        if name.lower() in ("bills receivable", "bills payable"):
            receivable = name.lower().endswith("receivable")
            today = tx.parse_date(end) or dt.date.today()
            rows = ""
            for bill in self._bills(end):
                if (bill["pending"] < 0) != receivable:
                    continue
                when = tx.parse_date(bill["date"])
                rows += (f"<BILLFIXED><BILLDATE>{when:%d-%b-%Y}</BILLDATE><BILLREF>{escape(bill['bill'])}</BILLREF>"
                         f"<BILLPARTY>{escape(bill['party'])}</BILLPARTY></BILLFIXED>"
                         f"<BILLCL>{bill['pending']:.2f}</BILLCL><BILLDUE>{when:%d-%b-%Y}</BILLDUE>"
                         f"<BILLOVERDUE>{(today - when).days}</BILLOVERDUE>")
            return f"<ENVELOPE>{rows}</ENVELOPE>"
        if name.lower() == "trial balance":
            rows = ""
            for ledger in self.masters["LEDGER"]:
                balance = self._ledger_balance(ledger, end, True)
                if balance:
                    rows += (f"<DSPACCNAME><DSPDISPNAME>{escape(ledger)}</DSPDISPNAME></DSPACCNAME><DSPACCINFO>"
                             f"<DSPCLDRAMT><DSPCLDRAMTA>{balance if balance < 0 else ''}</DSPCLDRAMTA></DSPCLDRAMT>"
                             f"<DSPCLCRAMT><DSPCLCRAMTA>{balance if balance > 0 else ''}</DSPCLCRAMTA></DSPCLCRAMT>"
                             "</DSPACCINFO>")
            return f"<ENVELOPE>{rows}</ENVELOPE>"
        return self._error(f"Could not find Report '{name}'!")

    @staticmethod
    def _error(message: str) -> str:
        return ("<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER><BODY><DATA>"
                f"<LINEERROR>{escape(message)}</LINEERROR></DATA></BODY></ENVELOPE>")

    # ---------------------------------------------------------------- import
    def _import(self, root: ET.Element) -> str:
        counts = {key: 0 for key in ("CREATED", "ALTERED", "DELETED", "CANCELLED", "ERRORS")}
        errors: list[str] = []
        last_voucher = "0"
        for message in root.iter("TALLYMESSAGE"):
            for element in list(message):
                try:
                    if element.tag == "VOUCHER":
                        last_voucher = self._import_voucher(element, counts)
                    elif element.tag in self.masters:
                        self._import_master(element, counts)
                    else:
                        raise ValueError(f"Unknown object {element.tag}")
                except ValueError as exc:
                    counts["ERRORS"] += 1
                    errors.append(str(exc))
        lines = "".join(f"<LINEERROR>{escape(e)}</LINEERROR>" for e in errors)
        fields = "".join(f"<{k}>{v}</{k}>" for k, v in counts.items())
        return ("<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DATA>"
                f"{lines}<IMPORTRESULT>{fields}<LASTVCHID>{last_voucher}</LASTVCHID><LASTMID>0</LASTMID>"
                "<COMBINED>0</COMBINED><IGNORED>0</IGNORED><EXCEPTIONS>0</EXCEPTIONS></IMPORTRESULT>"
                "</DATA></BODY></ENVELOPE>")

    def _import_master(self, element: ET.Element, counts: dict[str, int]) -> None:
        tag, name, action = element.tag, element.get("NAME", ""), (element.get("ACTION") or "Create").lower()
        label = tag.title()
        existing = self._find(tag, name)
        if action == "delete":
            if existing is None:
                raise ValueError(f"{label} '{name}' does not exist!")
            used = tag == "LEDGER" and any(n.lower() == name.lower() for v in self.vouchers
                                           for n, _ in self._voucher_ledgers(v))
            if used:
                raise ValueError(f"{label} '{name}' is used in vouchers and cannot be deleted!")
            del self.masters[tag][existing.get("NAME")]
            counts["DELETED"] += 1
            return
        parent = tx.text_of(element, "PARENT")
        if parent and tag == "LEDGER" and self._find("GROUP", parent) is None:
            raise ValueError(f"Group '{parent}' does not exist!")
        if tag == "STOCKITEM" and tx.text_of(element, "BASEUNITS") and self._find(
                "UNIT", tx.text_of(element, "BASEUNITS")) is None:
            raise ValueError(f"Unit '{tx.text_of(element, 'BASEUNITS')}' does not exist!")
        if existing is None:
            if action == "alter":
                raise ValueError(f"{label} '{name}' does not exist!")
            new = copy.deepcopy(element)
            new.attrib.pop("ACTION", None)
            self._ids(new)
            self.masters[tag][name] = new
            counts["CREATED"] += 1
            return
        for child in element:
            if child.tag == "LANGUAGENAME.LIST":
                continue
            for old in existing.findall(child.tag):
                existing.remove(old)
        for child in element:
            if child.tag != "LANGUAGENAME.LIST":
                existing.append(copy.deepcopy(child))
        new_name = tx.text_of(element, "NAME") or name
        if new_name != existing.get("NAME"):
            old_name = existing.get("NAME")
            del self.masters[tag][old_name]
            existing.set("NAME", new_name)
            self.masters[tag][new_name] = existing
            for voucher in self.vouchers:
                for node in voucher.iter("LEDGERNAME" if tag == "LEDGER" else "STOCKITEMNAME"):
                    if (node.text or "") == old_name:
                        node.text = new_name
                node = voucher.find("PARTYLEDGERNAME")
                if tag == "LEDGER" and node is not None and node.text == old_name:
                    node.text = new_name
        counts["ALTERED"] += 1

    def _locate(self, element: ET.Element) -> ET.Element | None:
        remote, tag_name, value = element.get("REMOTEID"), (element.get("TAGNAME") or "").upper(), element.get(
            "TAGVALUE")
        for voucher in self.vouchers:
            if remote and tx.text_of(voucher, "GUID") == remote:
                return voucher
            if tag_name.replace(" ", "") == "MASTERID" and tx.text_of(voucher, "MASTERID") == value:
                return voucher
            if tag_name.replace(" ", "") == "VOUCHERNUMBER" and tx.text_of(voucher, "VOUCHERNUMBER") == value \
                    and (tx.text_of(voucher, "VOUCHERTYPENAME") == element.get("VCHTYPE")):
                return voucher
        return None

    def _import_voucher(self, element: ET.Element, counts: dict[str, int]) -> str:
        action = (element.get("ACTION") or "Create").lower()
        if action in ("delete", "cancel"):
            found = self._locate(element)
            if found is None:
                raise ValueError("Voucher does not exist!")
            if action == "delete":
                self.vouchers.remove(found)
                counts["DELETED"] += 1
            else:
                node = found.find("ISCANCELLED")
                if node is None:
                    node = ET.SubElement(found, "ISCANCELLED")
                node.text = "Yes"
                counts["CANCELLED"] += 1
            return tx.text_of(found, "MASTERID")
        vch_type = tx.text_of(element, "VOUCHERTYPENAME") or element.get("VCHTYPE", "")
        if self._find("VOUCHERTYPE", vch_type) is None:
            raise ValueError(f"Voucher Type '{vch_type}' does not exist!")
        date = tx.text_of(element, "DATE")
        if not re.fullmatch(r"\d{8}", date) or date < f"{self.fy_start:%Y%m%d}":
            raise ValueError("Voucher date is missing or before the beginning of books!")
        total = Decimal(0)
        for name, value in self._voucher_ledgers(element):
            if self._find("LEDGER", name) is None:
                raise ValueError(f"Ledger '{name}' does not exist!")
            total += value
        for tag in ITEM_TAGS:
            for line in element.findall(tag):
                if self._find("STOCKITEM", tx.text_of(line, "STOCKITEMNAME")) is None:
                    raise ValueError(f"Stock Item '{tx.text_of(line, 'STOCKITEMNAME')}' does not exist!")
        if total != 0:
            raise ValueError(f"Voucher totals do not match! Dr and Cr differ by {abs(total):.2f}")
        new = copy.deepcopy(element)
        for attr in ("ACTION", "TAGNAME", "TAGVALUE", "DATE", "REMOTEID"):
            new.attrib.pop(attr, None)
        if action == "alter":
            found = self._locate(element)
            if found is None:
                raise ValueError("Voucher does not exist!")
            for tag in ("MASTERID", "GUID", "ALTERID"):
                for node in new.findall(tag):
                    new.remove(node)
                ET.SubElement(new, tag).text = tx.text_of(found, tag)
            self.vouchers[self.vouchers.index(found)] = new
            counts["ALTERED"] += 1
            return tx.text_of(new, "MASTERID")
        if not tx.text_of(new, "VOUCHERNUMBER"):
            same = [v for v in self.vouchers if tx.text_of(v, "VOUCHERTYPENAME") == vch_type]
            ET.SubElement(new, "VOUCHERNUMBER").text = str(len(same) + 1)
        self._ids(new)
        self.vouchers.append(new)
        counts["CREATED"] += 1
        return tx.text_of(new, "MASTERID")

    # ---------------------------------------------------------------- entry point
    def handle(self, text: str) -> str:
        """One XML request in, one XML response out."""
        with self.lock:
            self.requests.append(text)
            try:
                root = ET.fromstring(text.replace("&#4;", ""))
            except ET.ParseError:
                return "<RESPONSE>Unknown Request, cannot be processed</RESPONSE>"
            request = tx.text_of(root, "HEADER/TALLYREQUEST")
            kind, ident = tx.text_of(root, "HEADER/TYPE"), tx.text_of(root, "HEADER/ID")
            company = next((e.text for e in root.iter("SVCURRENTCOMPANY") if e.text), None)
            if company and company.lower() != self.company.lower():
                return self._error(f"Could not set 'SVCurrentCompany' to '{company}'")
            start = next((e.text for e in root.iter("SVFROMDATE") if e.text), None)
            end = next((e.text for e in root.iter("SVTODATE") if e.text), None)
            if request.lower() == "import":
                return self._import(root)
            if kind == "Collection":
                return self._collection(root, start, end)
            if kind == "Data":
                return self._report(ident, start, end)
            if kind == "Function":
                param = next((p.text for p in root.iter("PARAM")), "")
                answer = {"SerialNumber": "700000001", "IsEducationalMode": "No"}.get(param or "", "")
                return ("<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DATA>"
                        f'<RESULT TYPE="String">{answer}</RESULT></DATA></BODY></ENVELOPE>')
            return "<RESPONSE>Unknown Request, cannot be processed</RESPONSE>"

    # ---------------------------------------------------------------- seed data
    def _post(self, payload: str, kind: str) -> None:
        reply = self.handle(tx.import_xml(payload, kind, self.company))
        problems = re.findall(r"<LINEERROR>(.*?)</LINEERROR>", reply)
        if problems:
            raise RuntimeError(f"Demo seed failed: {problems}")

    def _seed(self) -> None:
        def master(tag: str, name: str, **fields: Any) -> None:
            self._post(builders.master_xml(tag, name, builders.extra_xml(fields)), "All Masters")

        primary = [("Capital Account", "No", "No"), ("Loans (Liability)", "No", "No"),
                   ("Current Liabilities", "No", "No"), ("Fixed Assets", "No", "Yes"),
                   ("Investments", "No", "Yes"), ("Current Assets", "No", "Yes"),
                   ("Branch / Divisions", "No", "No"), ("Misc. Expenses (ASSET)", "No", "Yes"),
                   ("Suspense A/c", "No", "No"), ("Sales Accounts", "Yes", "No"),
                   ("Purchase Accounts", "Yes", "Yes"), ("Direct Incomes", "Yes", "No"),
                   ("Indirect Incomes", "Yes", "No"), ("Direct Expenses", "Yes", "Yes"),
                   ("Indirect Expenses", "Yes", "Yes")]
        for name, revenue, positive in primary:
            element = ET.fromstring(f'<GROUP NAME="{escape(name)}"><PARENT>\x04 Primary</PARENT>'
                                    f"<ISREVENUE>{revenue}</ISREVENUE><ISDEEMEDPOSITIVE>{positive}"
                                    "</ISDEEMEDPOSITIVE></GROUP>".replace("\x04", "&amp;#4;"))
            self._ids(element)
            self.masters["GROUP"][name] = element
        for name, parent in (("Reserves & Surplus", "Capital Account"), ("Bank OD A/c", "Loans (Liability)"),
                             ("Secured Loans", "Loans (Liability)"), ("Unsecured Loans", "Loans (Liability)"),
                             ("Duties & Taxes", "Current Liabilities"), ("Provisions", "Current Liabilities"),
                             ("Sundry Creditors", "Current Liabilities"), ("Stock-in-Hand", "Current Assets"),
                             ("Deposits (Asset)", "Current Assets"),
                             ("Loans & Advances (Asset)", "Current Assets"),
                             ("Sundry Debtors", "Current Assets"), ("Cash-in-Hand", "Current Assets"),
                             ("Bank Accounts", "Current Assets")):
            master("GROUP", name, PARENT=parent)
        for name in ("Sales", "Purchase", "Payment", "Receipt", "Contra", "Journal", "Credit Note",
                     "Debit Note", "Stock Journal"):
            master("VOUCHERTYPE", name, PARENT=name, NUMBERINGMETHOD="Automatic", ISACTIVE="Yes")
        master("UNIT", "Nos", ISSIMPLEUNIT="Yes", ORIGINALNAME="Numbers", DECIMALPLACES=0)
        master("UNIT", "Kg", ISSIMPLEUNIT="Yes", ORIGINALNAME="Kilograms", DECIMALPLACES=3)
        master("GODOWN", "Main Location")
        master("STOCKGROUP", "Valves")
        master("COSTCENTRE", "Head Office", CATEGORY="Primary Cost Category")

        def ledger(name: str, group: str, **options: Any) -> None:
            self._post(builders.master_xml("LEDGER", name, builders.ledger_body(parent=group, **options)),
                       "All Masters")

        ledger("Cash", "Cash-in-Hand", opening_balance=50000, opening_type="Dr")
        ledger("HDFC Bank", "Bank Accounts", opening_balance=500000, opening_type="Dr")
        ledger("Capital Account - Proprietor", "Capital Account", opening_balance=556000, opening_type="Cr")
        ledger("Sales @ 18%", "Sales Accounts")
        ledger("Purchase @ 18%", "Purchase Accounts")
        ledger("CGST", "Duties & Taxes", tax_type="GST", gst_duty_head="CGST")
        ledger("SGST", "Duties & Taxes", tax_type="GST", gst_duty_head="SGST/UTGST")
        ledger("IGST", "Duties & Taxes", tax_type="GST", gst_duty_head="IGST")
        ledger("TDS Payable 194C", "Duties & Taxes", tax_type="TDS")
        ledger("Rent", "Indirect Expenses")
        ledger("Freight Charges", "Direct Expenses")
        ledger("Round Off", "Indirect Expenses")
        ledger("Shree Ganesh Hardware", "Sundry Debtors", gstin=gstin("24AABCS1234K1Z"), state="Gujarat",
               country="India", bill_wise=True, address=["Relief Road", "Ahmedabad"], pincode="380001")
        ledger("Patel Engineering Works", "Sundry Debtors", gstin=gstin("27AAGFP4321M1Z"), state="Maharashtra",
               country="India", bill_wise=True)
        ledger("Walk-in Customer", "Sundry Debtors", state="Gujarat")
        ledger("Ambika Steel Suppliers", "Sundry Creditors", gstin=gstin("24AAECA9876B1Z"), state="Gujarat",
               country="India", bill_wise=True)
        ledger("Mahavir Transport", "Sundry Creditors", pan="ABCPM1234F", state="Gujarat", bill_wise=True)

        def item(name: str, unit: str, hsn: str, **options: Any) -> None:
            body = builders.stock_item_body(parent="Valves" if "Valve" in name else None, unit=unit, hsn=hsn,
                                            gst_rate=18, **options)
            self._post(builders.master_xml("STOCKITEM", name, body), "All Masters")

        item("Ball Valve 2 inch", "Nos", "848180", opening_qty=20, opening_rate=300)
        item("Gate Valve 4 inch", "Nos", "848180")
        item("Steel Rod", "Kg", "7214")

        def day(offset: int) -> dt.date:
            return self.fy_start + dt.timedelta(days=offset)

        local = {"cgst_ledger": "CGST", "sgst_ledger": "SGST", "igst_ledger": "IGST", "interstate": False}
        inter = {**local, "interstate": True}

        def invoice(kind: str, when: dt.date, party: str, items: list[dict[str, Any]], gst: dict[str, Any],
                    **options: Any) -> None:
            ledger_name = "Sales @ 18%" if kind == "sales" else "Purchase @ 18%"
            self._post(builders.invoice_xml(kind, when, party, items, ledger=ledger_name, gst=gst,
                                            **options)["xml"], "Vouchers")

        def voucher(kind: str, when: dt.date, entries: list[dict[str, Any]], **options: Any) -> None:
            self._post(builders.accounting_voucher_xml(kind, when, entries, **options), "Vouchers")

        invoice("purchase", day(2), "Ambika Steel Suppliers",
                [{"item": "Ball Valve 2 inch", "qty": 100, "unit": "Nos", "rate": 300, "gst_rate": 18},
                 {"item": "Steel Rod", "qty": 250, "unit": "Kg", "rate": 60, "gst_rate": 18}],
                local, reference="ASS/101", narration="Purchase of valves and rods")
        invoice("sales", day(5), "Shree Ganesh Hardware",
                [{"item": "Ball Valve 2 inch", "qty": 10, "unit": "Nos", "rate": 450, "gst_rate": 18}],
                local, number="S-001", narration="Sale of ball valves")
        invoice("sales", day(9), "Patel Engineering Works",
                [{"item": "Ball Valve 2 inch", "qty": 5, "unit": "Nos", "rate": 460, "gst_rate": 18}],
                inter, number="S-002", place_of_supply="Maharashtra", narration="Interstate sale")
        invoice("sales", day(12), "Walk-in Customer",
                [{"item": "Steel Rod", "qty": 40, "unit": "Kg", "rate": 75, "gst_rate": 18}],
                local, number="S-003", narration="Counter sale")
        voucher("Receipt", day(15), [{"ledger": "HDFC Bank", "dr": 5310},
                                     {"ledger": "Shree Ganesh Hardware", "cr": 5310,
                                      "bills": [{"name": "S-001", "type": "Agst Ref", "amount": 5310}]}],
                party="Shree Ganesh Hardware", narration="NEFT received against S-001")
        voucher("Payment", day(18), [{"ledger": "Rent", "dr": 15000}, {"ledger": "Cash", "cr": 15000}],
                narration="Shop rent paid in cash")
        voucher("Payment", day(20), [{"ledger": "Ambika Steel Suppliers", "dr": 30000,
                                      "bills": [{"name": "ASS/101", "type": "Agst Ref", "amount": 30000}]},
                                     {"ledger": "HDFC Bank", "cr": 30000,
                                      "bank": {"transaction_type": "Cheque", "instrument_number": "000451",
                                               "favouring": "Ambika Steel Suppliers"}}],
                party="Ambika Steel Suppliers", narration="Part payment by cheque 000451")
        voucher("Journal", day(22), [{"ledger": "Freight Charges", "dr": 20000},
                                     {"ledger": "Mahavir Transport", "cr": 19600,
                                      "bills": [{"name": "MT/55", "type": "New Ref", "amount": 19600}]},
                                     {"ledger": "TDS Payable 194C", "cr": 400}],
                party="Mahavir Transport", narration="Freight bill MT/55, TDS u/s 194C @ 2%")
        voucher("Contra", day(25), [{"ledger": "HDFC Bank", "dr": 10000}, {"ledger": "Cash", "cr": 10000}],
                narration="Cash deposited into bank")


class _Handler(BaseHTTPRequestHandler):
    tally: DemoTally

    def _send(self, text: str, encoding: str) -> None:
        body = text.encode(encoding)
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._send("<RESPONSE>TallyPrime Server is Running</RESPONSE>", "utf-8")

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        wide = "utf-16" in (self.headers.get("Content-Type") or "").lower()
        self._send(self.tally.handle(raw.decode("utf-16-le" if wide else "utf-8")),
                   "utf-16-le" if wide else "utf-8")

    def log_message(self, *args: Any) -> None:
        pass


def start(port: int = 9000, tally: DemoTally | None = None) -> tuple[ThreadingHTTPServer, DemoTally]:
    """Start the pretend Tally in a background thread. Returns (http server, data)."""
    data = tally or DemoTally()
    handler = type("Handler", (_Handler,), {"tally": data})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, data


def serve(port: int = 9000) -> None:
    server, data = start(port)
    print(f"Demo Tally running on http://localhost:{server.server_address[1]}  (company: {data.company})")
    print("Press Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
