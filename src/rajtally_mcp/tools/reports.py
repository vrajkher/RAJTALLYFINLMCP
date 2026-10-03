"""Financial reports, built from ledger balances and vouchers so the numbers are easy to trace.

Tally's own printed reports are always available through tally_native_report.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from .. import core
from .. import tally_xml as tx
from ..tally_xml import TallyError
from . import READ

NATURE = {
    "capital account": "liability", "loans (liability)": "liability", "current liabilities": "liability",
    "suspense a/c": "liability", "branch / divisions": "liability",
    "fixed assets": "asset", "investments": "asset", "current assets": "asset",
    "misc. expenses (asset)": "asset",
    "sales accounts": "income", "direct incomes": "income", "indirect incomes": "income",
    "purchase accounts": "expense", "direct expenses": "expense", "indirect expenses": "expense",
}
TRADING = {"sales accounts", "purchase accounts", "direct incomes", "direct expenses"}
STOCK_GROUP = "stock-in-hand"

NATIVE_REPORTS = [
    "Trial Balance", "Balance Sheet", "Profit and Loss", "Day Book", "Cash Flow", "Funds Flow",
    "Ratio Analysis", "Stock Summary", "Group Summary", "Group Vouchers", "Ledger Vouchers",
    "Ledger Outstandings", "Group Outstandings", "Bills Receivable", "Bills Payable", "Cash/Bank Books",
    "Sales Register", "Purchase Register", "Journal Register", "Payment Register", "Receipt Register",
    "Voucher Register", "List of Accounts", "Stock Vouchers", "Godown Summary", "Movement Analysis",
    "Stock Ageing Analysis", "Reorder Status", "Cost Centre Summary", "Cost Category Summary",
    "Budget Variance", "Interest Calculation", "Bank Reconciliation", "Statistics", "Exception Reports",
    "GSTR-1", "GSTR-2", "GSTR-3B", "GST Annual Computation", "Form 26Q", "Form 27Q", "TDS Outstandings",
    "Pay Sheet", "Payroll Statement", "Payslip", "Attendance Sheet", "Employee Profile",
]


def signed(row: dict[str, Any], field: str) -> Decimal:
    """Back from (amount, Dr/Cr) to Tally's signed amount: debit negative, credit positive."""
    value = Decimal(str(row.get(field) or 0))
    return -value if row.get(field + "_type") == "Dr" else value


def chain(group: str, groups: dict[str, dict[str, Any]]) -> list[str]:
    """The group and all its ancestors, lower-cased."""
    names, seen = [], set()
    while group and group not in seen:
        seen.add(group)
        names.append(group.lower())
        group = groups.get(group, {}).get("parent", "")
    return names


def nature_of(primary: str, groups: dict[str, dict[str, Any]]) -> str:
    known = NATURE.get(primary.lower())
    if known:
        return known
    group = groups.get(primary, {})
    if group.get("is_revenue"):
        return "expense" if group.get("is_deemed_positive") else "income"
    return "asset" if group.get("is_deemed_positive") else "liability"


def books(start: dt.date, end: dt.date, company: str | None) -> dict[str, Any]:
    """Every ledger with signed opening / closing balance, its group chain and nature."""
    groups = core.group_tree(company)
    ledgers = []
    for row in core.list_masters("ledger", company=company, from_date=start, to_date=end):
        parent = row.get("parent", "")
        # ledgers directly under Primary (e.g. "Profit & Loss A/c") stand as their own head
        primary = core.primary_of(parent, groups) if parent in groups else row["name"]
        ledgers.append({
            "name": row["name"], "group": parent, "primary_group": primary,
            "nature": nature_of(primary, groups), "chain": chain(parent, groups),
            "opening": signed(row, "opening_balance"), "closing": signed(row, "closing_balance"),
            "gstin": row.get("gstin", ""), "pan": row.get("pan", ""), "state": row.get("state", ""),
            "tax_type": row.get("tax_type", ""), "gst_duty_head": row.get("gst_duty_head", ""),
        })
    return {"groups": groups, "ledgers": ledgers}


def stock_values(start: dt.date, end: dt.date, company: str | None, ledgers: list[dict[str, Any]]
                 ) -> tuple[Decimal, Decimal, str]:
    """Opening and closing stock value, and where the figure came from."""
    try:
        items = core.list_masters("stock_item", company=company, from_date=start, to_date=end)
    except TallyError:
        items = []
    opening = sum((Decimal(str(i.get("opening_value") or 0)) for i in items), Decimal(0))
    closing = sum((Decimal(str(i.get("closing_value") or 0)) for i in items), Decimal(0))
    if opening or closing:
        return opening, closing, "stock item valuation"
    stock_ledgers = [l for l in ledgers if STOCK_GROUP in l["chain"]]
    return (sum((-l["opening"] for l in stock_ledgers), Decimal(0)),
            sum((-l["closing"] for l in stock_ledgers), Decimal(0)), "stock-in-hand ledgers")


def profit_and_loss(start: dt.date, end: dt.date, company: str | None,
                    data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or books(start, end, company)
    ledgers = data["ledgers"]
    opening_stock, closing_stock, stock_basis = stock_values(start, end, company, ledgers)
    heads: dict[str, dict[str, Any]] = {}
    for ledger in ledgers:
        if ledger["nature"] not in ("income", "expense"):
            continue
        movement = ledger["closing"] - ledger["opening"]
        if not movement:
            continue
        head = heads.setdefault(ledger["primary_group"], {"nature": ledger["nature"], "total": Decimal(0),
                                                         "ledgers": []})
        value = movement if ledger["nature"] == "income" else -movement
        head["total"] += value
        head["ledgers"].append({"ledger": ledger["name"], "group": ledger["group"], "amount": tx.money(value)})

    def total(nature: str, trading: bool) -> Decimal:
        return sum((h["total"] for name, h in heads.items()
                    if h["nature"] == nature and (_is_trading(name, data["groups"]) == trading)), Decimal(0))

    direct_income, direct_expense = total("income", True), total("expense", True)
    indirect_income, indirect_expense = total("income", False), total("expense", False)
    gross = direct_income + closing_stock - opening_stock - direct_expense
    net = gross + indirect_income - indirect_expense
    return {
        "from_date": start.isoformat(), "to_date": end.isoformat(),
        "opening_stock": tx.money(opening_stock), "closing_stock": tx.money(closing_stock),
        "stock_basis": stock_basis,
        "trading_income": tx.money(direct_income), "trading_expense": tx.money(direct_expense),
        "gross_profit": tx.money(gross),
        "indirect_income": tx.money(indirect_income), "indirect_expense": tx.money(indirect_expense),
        "net_profit": tx.money(net),
        "heads": [{"head": name, "nature": h["nature"],
                   "section": "trading" if _is_trading(name, data["groups"]) else "profit_and_loss",
                   "total": tx.money(h["total"]),
                   "ledgers": sorted(h["ledgers"], key=lambda r: -abs(r["amount"]))}
                  for name, h in sorted(heads.items())],
        "_net": net, "_closing_stock": closing_stock,
    }


def _is_trading(primary: str, groups: dict[str, dict[str, Any]]) -> bool:
    if primary.lower() in NATURE:
        return primary.lower() in TRADING
    return bool(groups.get(primary, {}).get("affects_gross_profit"))


def ledger_row(name: str, start: dt.date, end: dt.date, company: str | None) -> dict[str, Any]:
    spec = core.master_spec("ledger")
    elements = tx.collection("Ledger", spec["fields"], filters={"RajByName": f"$Name = {tx.tdl_string(name)}"},
                             company=company, from_date=start, to_date=end)
    for element in elements:
        row = core.normalise_master("ledger", element)
        if row["name"].lower() == name.strip().lower():
            return row
    raise TallyError(f"No ledger named '{name}'. Use tally_search to find the exact name.")


def bucket_label(days: int, edges: list[int]) -> str:
    low = 0
    for edge in edges:
        if days <= edge:
            return f"{low}-{edge} days"
        low = edge + 1
    return f"over {edges[-1]} days"


def register(mcp: Any) -> None:

    @mcp.tool(annotations=READ)
    def tally_trial_balance(from_date: str | None = None, to_date: str | None = None,
                            level: str = "ledger", group: str | None = None, include_zero: bool = False,
                            company: str | None = None) -> dict[str, Any]:
        """Trial balance: opening and closing balance of every ledger (Dr / Cr columns) with totals.

        level: 'ledger' (default), 'group' (immediate group) or 'primary' (top-level heads).
        group: only ledgers under this group. Dates blank = current financial year."""
        start, end = tx.period(from_date, to_date)
        data = books(start, end, company)
        ledgers = data["ledgers"]
        if group:
            want = group.strip().lower()
            ledgers = [l for l in ledgers if want in l["chain"]]
            if not ledgers:
                raise TallyError(f"No ledgers under group '{group}'. Check the group name with tally_search.")
        if not include_zero:
            ledgers = [l for l in ledgers if l["opening"] or l["closing"]]
        key = {"ledger": "name", "group": "group", "primary": "primary_group"}.get(level.strip().lower())
        if not key:
            raise TallyError("level must be 'ledger', 'group' or 'primary'.")
        merged: dict[str, dict[str, Any]] = {}
        for ledger in ledgers:
            row = merged.setdefault(ledger[key], {"opening": Decimal(0), "closing": Decimal(0),
                                                  "group": ledger["group"], "primary": ledger["primary_group"]})
            row["opening"] += ledger["opening"]
            row["closing"] += ledger["closing"]
        rows = []
        for name, row in sorted(merged.items(), key=lambda kv: (kv[1]["primary"], kv[0].lower())):
            out: dict[str, Any] = {"name": name}
            if key == "name":
                out["group"] = row["group"]
            if key != "primary_group":
                out["primary_group"] = row["primary"]
            opening, closing = tx.dr_cr(row["opening"]), tx.dr_cr(row["closing"])
            out.update(opening_dr=opening["dr"], opening_cr=opening["cr"], closing_dr=closing["dr"],
                       closing_cr=closing["cr"])
            rows.append(out)
        if not group:
            # Tally shows stock kept in inventory records as an "Opening Stock" line on the debit side
            opening_stock, _, basis = stock_values(start, end, company, data["ledgers"])
            if opening_stock and basis == "stock item valuation":
                rows.append({"name": "Opening Stock", "primary_group": "Current Assets",
                             "opening_dr": tx.money(opening_stock), "opening_cr": 0.0,
                             "closing_dr": tx.money(opening_stock), "closing_cr": 0.0,
                             "note": "from stock item opening values"})
        total_dr = sum(Decimal(str(r["closing_dr"])) for r in rows)
        total_cr = sum(Decimal(str(r["closing_cr"])) for r in rows)
        return {"from_date": start.isoformat(), "to_date": end.isoformat(), "level": level,
                "total_closing_dr": tx.money(total_dr), "total_closing_cr": tx.money(total_cr),
                "difference": tx.money(abs(total_dr - total_cr)), "count": len(rows), "rows": rows}

    @mcp.tool(annotations=READ)
    def tally_balance_sheet(as_of: str | None = None, from_date: str | None = None, detailed: bool = False,
                            company: str | None = None) -> dict[str, Any]:
        """Balance sheet as on a date: liabilities and assets by head, with net profit for the year
        and closing stock. detailed=true lists the ledgers under each head.

        'difference' should be 0. If it is not, the books have a difference in opening balances, or stock
        is valued in a way this summary cannot see - compare with tally_native_report('Balance Sheet')."""
        end = tx.parse_date(as_of) or tx.period(None, None)[1]
        start = tx.parse_date(from_date) or tx.financial_year(end)[0]
        data = books(start, end, company)
        pnl = profit_and_loss(start, end, company, data)
        sides: dict[str, dict[str, dict[str, Any]]] = {"liability": {}, "asset": {}}
        for ledger in data["ledgers"]:
            if ledger["nature"] not in sides or not ledger["closing"]:
                continue
            if STOCK_GROUP in ledger["chain"]:
                continue  # replaced by the closing stock valuation below
            head = sides[ledger["nature"]].setdefault(ledger["primary_group"],
                                                      {"total": Decimal(0), "ledgers": []})
            value = ledger["closing"] if ledger["nature"] == "liability" else -ledger["closing"]
            head["total"] += value
            head["ledgers"].append({"ledger": ledger["name"], "group": ledger["group"],
                                    "amount": tx.money(value)})
        if pnl["_closing_stock"]:
            sides["asset"]["Closing Stock"] = {"total": pnl["_closing_stock"], "ledgers": []}
        sides["liability"]["Profit & Loss A/c (current period)"] = {"total": pnl["_net"], "ledgers": []}

        def render(side: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
            out = []
            for name, head in sorted(side.items()):
                row: dict[str, Any] = {"head": name, "amount": tx.money(head["total"])}
                if detailed and head["ledgers"]:
                    row["ledgers"] = sorted(head["ledgers"], key=lambda r: -abs(r["amount"]))
                out.append(row)
            return out

        total_l = sum((h["total"] for h in sides["liability"].values()), Decimal(0))
        total_a = sum((h["total"] for h in sides["asset"].values()), Decimal(0))
        return {"as_of": end.isoformat(), "profit_period_from": start.isoformat(),
                "liabilities": render(sides["liability"]), "total_liabilities": tx.money(total_l),
                "assets": render(sides["asset"]), "total_assets": tx.money(total_a),
                "difference": tx.money(abs(total_l - total_a)), "net_profit": pnl["net_profit"],
                "closing_stock": pnl["closing_stock"], "stock_basis": pnl["stock_basis"]}

    @mcp.tool(annotations=READ)
    def tally_profit_loss(from_date: str | None = None, to_date: str | None = None, detailed: bool = False,
                          company: str | None = None) -> dict[str, Any]:
        """Profit & loss for a period: trading account (sales, purchases, direct items, stock) down to
        gross profit, then indirect income and expenses down to net profit.
        detailed=true lists the ledgers under each head."""
        start, end = tx.period(from_date, to_date)
        result = profit_and_loss(start, end, company)
        result.pop("_net")
        result.pop("_closing_stock")
        if not detailed:
            for head in result["heads"]:
                head.pop("ledgers")
        return result

    @mcp.tool(annotations=READ)
    def tally_ledger_statement(ledger: str, from_date: str | None = None, to_date: str | None = None,
                               limit: int = 500, offset: int = 0, company: str | None = None
                               ) -> dict[str, Any]:
        """Ledger account statement: opening balance, every transaction with running balance, closing
        balance. Works for any ledger - party, bank, cash, expense, tax."""
        start, end = tx.period(from_date, to_date)
        row = ledger_row(ledger, start, end, company)
        name = row["name"]
        running = signed(row, "opening_balance")
        opening = running
        lines, total_dr, total_cr = [], Decimal(0), Decimal(0)
        for voucher in core.fetch_vouchers(start, end, company=company):
            if voucher["cancelled"] or voucher["optional"]:
                continue
            mine = [e for e in voucher["entries"] if e["ledger"].lower() == name.lower()]
            if not mine:
                continue
            debit = sum(Decimal(str(e["dr"])) for e in mine)
            credit = sum(Decimal(str(e["cr"])) for e in mine)
            others = sorted({e["ledger"] for e in voucher["entries"] if e["ledger"].lower() != name.lower()})
            running += credit - debit
            total_dr += debit
            total_cr += credit
            balance = tx.dr_cr(running)
            lines.append({
                "date": voucher["date"], "type": voucher["type"], "number": voucher["number"],
                "particulars": others[0] if len(others) == 1 else ", ".join(others[:4])
                + (" ..." if len(others) > 4 else ""),
                "dr": tx.money(debit), "cr": tx.money(credit),
                "balance": balance["dr"] or balance["cr"], "balance_type": "Dr" if running < 0 else "Cr"
                if running > 0 else "", "narration": voucher["narration"], "master_id": voucher["master_id"],
            })
        closing_books = signed(row, "closing_balance")
        result = {
            "ledger": name, "group": row.get("parent", ""), "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "opening_balance": tx.money(abs(opening)), "opening_type": "Dr" if opening < 0 else "Cr",
            "total_dr": tx.money(total_dr), "total_cr": tx.money(total_cr),
            "closing_balance": tx.money(abs(running)), "closing_type": "Dr" if running < 0 else "Cr",
            **core.page(lines, limit, offset, "transactions"),
        }
        if abs(closing_books - running) > Decimal("0.01"):
            result["warning"] = (f"Closing balance in Tally is {tx.money(abs(closing_books))} "
                                 f"{'Dr' if closing_books < 0 else 'Cr'}; the transactions listed add up "
                                 "differently. Some vouchers may be outside this period filter.")
        return result

    @mcp.tool(annotations=READ)
    def tally_outstandings(kind: str = "receivable", party: str | None = None, as_of: str | None = None,
                           ageing_days: list[int] | None = None, company: str | None = None
                           ) -> dict[str, Any]:
        """Pending bills: money to receive from customers (kind='receivable') or to pay to suppliers
        (kind='payable'), bill by bill, with overdue days, ageing buckets and party-wise totals.
        party: only this party. ageing_days: bucket edges, default [30, 60, 90, 180]."""
        receivable = not kind.strip().lower().startswith("p")
        end = tx.parse_date(as_of) or dt.date.today()
        start = tx.financial_year(end)[0]
        edges = sorted(ageing_days or [30, 60, 90, 180])
        bills: list[dict[str, Any]] = []
        root = tx.native_report("Bills Receivable" if receivable else "Bills Payable", company=company,
                                from_date=start, to_date=end)
        for row in tx.flat_rows(root):
            if "BILLPARTY" not in row and "BILLREF" not in row:
                continue
            pending = tx.amount(row.get("BILLCL") or row.get("BILLFINAL"))
            bills.append({"party": row.get("BILLPARTY", ""), "bill": row.get("BILLREF", ""),
                          "bill_date": tx.iso_date(row.get("BILLDATE")), "due_date": tx.iso_date(row.get("BILLDUE")),
                          "pending": tx.money(abs(pending)),
                          "overdue_days": int(tx.amount(row.get("BILLOVERDUE"))) if row.get("BILLOVERDUE") else None})
        source = "Tally bills report"
        if not bills:  # fall back to the Bills collection
            source = "Tally bills collection"
            for element in tx.collection("Bills", ["Name", "Parent", "BillDate", "ClosingBalance",
                                                   "BillCreditPeriod"], company=company, from_date=start,
                                         to_date=end):
                pending = tx.amount(tx.text_of(element, "CLOSINGBALANCE"))
                if not pending or (pending < 0) != receivable:
                    continue
                bills.append({"party": tx.text_of(element, "PARENT"),
                              "bill": element.get("NAME") or tx.text_of(element, "NAME"),
                              "bill_date": tx.iso_date(tx.text_of(element, "BILLDATE")), "due_date": "",
                              "pending": tx.money(abs(pending)), "overdue_days": None})
        if party:
            want = party.strip().lower()
            bills = [b for b in bills if b["party"].lower() == want]
        buckets: dict[str, Decimal] = {}
        parties: dict[str, dict[str, Any]] = {}
        for bill in bills:
            reference = tx.parse_date(bill["due_date"] or bill["bill_date"]) if (bill["due_date"] or bill["bill_date"]) else None
            age = (end - reference).days if reference else 0
            if bill["overdue_days"] is None:
                bill["overdue_days"] = max(age, 0)
            label = bucket_label(max(bill["overdue_days"], 0), edges) if bill["overdue_days"] > 0 else "not due"
            bill["ageing"] = label
            buckets[label] = buckets.get(label, Decimal(0)) + Decimal(str(bill["pending"]))
            entry = parties.setdefault(bill["party"], {"party": bill["party"], "pending": Decimal(0), "bills": 0,
                                                       "oldest_bill_date": bill["bill_date"]})
            entry["pending"] += Decimal(str(bill["pending"]))
            entry["bills"] += 1
            if bill["bill_date"] and (not entry["oldest_bill_date"] or bill["bill_date"] < entry["oldest_bill_date"]):
                entry["oldest_bill_date"] = bill["bill_date"]
        party_rows = sorted(({**p, "pending": tx.money(p["pending"])} for p in parties.values()),
                            key=lambda r: -r["pending"])
        bills.sort(key=lambda b: (-(b["overdue_days"] or 0), b["party"]))
        return {"kind": "receivable" if receivable else "payable", "as_of": end.isoformat(), "source": source,
                "total_pending": tx.money(sum(Decimal(str(b["pending"])) for b in bills)),
                "ageing": {k: tx.money(v) for k, v in buckets.items()}, "parties": party_rows,
                "bill_count": len(bills), "bills": bills[:1000]}

    @mcp.tool(annotations=READ)
    def tally_cash_bank_balances(as_of: str | None = None, company: str | None = None) -> dict[str, Any]:
        """Cash and bank position as on a date: every cash, bank and bank overdraft ledger with its
        balance, and the net total."""
        end = tx.parse_date(as_of) or dt.date.today()
        data = books(tx.financial_year(end)[0], end, company)
        rows, net = [], Decimal(0)
        for ledger in data["ledgers"]:
            kind = next((k for k in ("cash-in-hand", "bank accounts", "bank od a/c", "bank occ a/c")
                         if k in ledger["chain"]), None)
            if not kind:
                continue
            balance = -ledger["closing"]  # positive = money we have
            net += balance
            rows.append({"ledger": ledger["name"], "kind": "cash" if kind == "cash-in-hand" else
                         "bank" if kind == "bank accounts" else "overdraft", "balance": tx.money(balance)})
        rows.sort(key=lambda r: (r["kind"], -r["balance"]))
        return {"as_of": end.isoformat(), "net_cash_and_bank": tx.money(net), "ledgers": rows,
                "note": "A negative balance means overdrawn / credit balance."}

    @mcp.tool(annotations=READ)
    def tally_group_summary(group: str, from_date: str | None = None, to_date: str | None = None,
                            company: str | None = None) -> dict[str, Any]:
        """Drill into one group: its sub-groups and ledgers with opening and closing balances.
        Examples: 'Sundry Debtors', 'Indirect Expenses', 'Duties & Taxes', 'Current Assets'."""
        start, end = tx.period(from_date, to_date)
        data = books(start, end, company)
        exact = next((g for g in data["groups"] if g.lower() == group.strip().lower()), None)
        if not exact:
            raise TallyError(f"No group named '{group}'. Use tally_search to find the exact name.")
        rows: dict[tuple[str, str], dict[str, Decimal]] = {}
        for ledger in data["ledgers"]:
            names = ledger["chain"]
            if exact.lower() not in names:
                continue
            depth = names.index(exact.lower())
            if depth == 0:
                key = ("ledger", ledger["name"])
            else:  # the child group directly under the requested group
                child = names[depth - 1]
                key = ("group", next(g for g in data["groups"] if g.lower() == child))
            row = rows.setdefault(key, {"opening": Decimal(0), "closing": Decimal(0)})
            row["opening"] += ledger["opening"]
            row["closing"] += ledger["closing"]
        out = []
        for (kind, name), row in sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1].lower())):
            if not row["opening"] and not row["closing"]:
                continue
            opening, closing = tx.dr_cr(row["opening"]), tx.dr_cr(row["closing"])
            out.append({"type": kind, "name": name, "opening_dr": opening["dr"], "opening_cr": opening["cr"],
                        "closing_dr": closing["dr"], "closing_cr": closing["cr"]})
        net = sum((r["closing"] for r in rows.values()), Decimal(0))
        return {"group": exact, "from_date": start.isoformat(), "to_date": end.isoformat(),
                "closing_balance": tx.money(abs(net)), "closing_type": "Dr" if net < 0 else "Cr",
                "count": len(out), "rows": out}

    @mcp.tool(annotations=READ)
    def tally_monthly_summary(ledger: str | None = None, group: str | None = None,
                              from_date: str | None = None, to_date: str | None = None,
                              company: str | None = None) -> dict[str, Any]:
        """Month-by-month debit and credit totals for one ledger or one whole group - e.g. monthly
        sales ('Sales Accounts'), monthly expenses ('Indirect Expenses') or a bank ledger."""
        if not ledger and not group:
            raise TallyError("Give a ledger or a group.")
        start, end = tx.period(from_date, to_date)
        data = books(start, end, company)
        if ledger:
            members = {l["name"].lower() for l in data["ledgers"] if l["name"].lower() == ledger.strip().lower()}
        else:
            members = {l["name"].lower() for l in data["ledgers"] if group.strip().lower() in l["chain"]}
        if not members:
            raise TallyError("Nothing matched that ledger / group name. Use tally_search to check it.")
        months: dict[str, dict[str, Decimal]] = {}
        for voucher in core.fetch_vouchers(start, end, company=company):
            if voucher["cancelled"] or voucher["optional"]:
                continue
            for entry in voucher["entries"]:
                if entry["ledger"].lower() in members:
                    month = months.setdefault(voucher["date"][:7], {"dr": Decimal(0), "cr": Decimal(0)})
                    month["dr"] += Decimal(str(entry["dr"]))
                    month["cr"] += Decimal(str(entry["cr"]))
        rows = [{"month": m, "dr": tx.money(v["dr"]), "cr": tx.money(v["cr"]),
                 "net": tx.money(abs(v["cr"] - v["dr"])), "net_type": "Cr" if v["cr"] >= v["dr"] else "Dr"}
                for m, v in sorted(months.items())]
        return {"for": ledger or group, "from_date": start.isoformat(), "to_date": end.isoformat(),
                "total_dr": tx.money(sum(v["dr"] for v in months.values())),
                "total_cr": tx.money(sum(v["cr"] for v in months.values())), "months": rows}

    @mcp.tool(annotations=READ)
    def tally_native_report(report: str, from_date: str | None = None, to_date: str | None = None,
                            variables: dict[str, str] | None = None, raw: bool = False, limit: int = 500,
                            company: str | None = None) -> dict[str, Any]:
        """Export any of Tally's own reports exactly as Tally computes it, by its report name.

        Common names: Trial Balance, Balance Sheet, Profit and Loss, Cash Flow, Funds Flow,
        Ratio Analysis, Stock Summary, Bills Receivable, Bills Payable, Sales Register, Purchase Register,
        Ledger Vouchers, GSTR-1, GSTR-3B, Pay Sheet (full list: tally_list_native_reports).
        variables: report options as Tally variables, e.g. {"LEDGERNAME": "Cash"} for Ledger Vouchers,
        {"GROUPNAME": "Sundry Debtors"} for Group Summary, {"STOCKITEMNAME": "Ball Valve"} for
        Stock Vouchers. raw=true returns the XML text instead of rows."""
        start, end = tx.period(from_date, to_date)
        if raw:
            text = tx.post(tx.report_xml(report, company=company, from_date=start, to_date=end,
                                         variables=variables))
            tx.parse(text)
            return {"report": report, "xml": text[:200000], "truncated": len(text) > 200000}
        root = tx.native_report(report, company=company, from_date=start, to_date=end, variables=variables)
        rows = tx.flat_rows(root)
        return {"report": report, "from_date": start.isoformat(), "to_date": end.isoformat(),
                **core.page(rows, limit, 0, "rows"),
                "note": "Column names are Tally's own display tags. Use raw=true to see the XML."}

    @mcp.tool(annotations=READ)
    def tally_list_native_reports() -> dict[str, Any]:
        """Names of Tally's built-in reports that tally_native_report can export. Any other report or
        custom TDL report name available in your Tally also works."""
        return {"reports": NATIVE_REPORTS}
