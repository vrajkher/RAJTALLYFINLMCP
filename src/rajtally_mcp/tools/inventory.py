"""Inventory: what is in stock, what it is worth, and how each item moved."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .. import core
from .. import tally_xml as tx
from ..tally_xml import TallyError
from . import READ


def register(mcp: Any) -> None:

    @mcp.tool(annotations=READ)
    def tally_stock_summary(as_of: str | None = None, from_date: str | None = None,
                            stock_group: str | None = None, search: str | None = None,
                            only_in_stock: bool = False, limit: int = 300, offset: int = 0,
                            company: str | None = None) -> dict[str, Any]:
        """Stock summary: every item with opening and closing quantity, rate and value, HSN and GST rate,
        plus the total stock value. Flags items with negative stock.
        stock_group: only items under this stock group. only_in_stock=true hides nil-stock items."""
        end = tx.parse_date(as_of) or tx.period(None, None)[1]
        start = tx.parse_date(from_date) or tx.financial_year(end)[0]
        rows = core.list_masters("stock_item", parent=stock_group, search=search, company=company,
                                 from_date=start, to_date=end)
        if only_in_stock:
            rows = [r for r in rows if r.get("closing_qty")]
        negative = [r["name"] for r in rows if (r.get("closing_qty") or 0) < 0]
        total = sum(Decimal(str(r.get("closing_value") or 0)) for r in rows)
        opening = sum(Decimal(str(r.get("opening_value") or 0)) for r in rows)
        for row in rows:
            row.pop("master_id", None)
            row.pop("guid", None)
        result = {"as_of": end.isoformat(), "opening_stock_value": tx.money(opening),
                  "closing_stock_value": tx.money(total), **core.page(rows, limit, offset, "items")}
        if negative:
            result["negative_stock_items"] = negative
        return result

    @mcp.tool(annotations=READ)
    def tally_stock_movement(item: str, from_date: str | None = None, to_date: str | None = None,
                             limit: int = 500, company: str | None = None) -> dict[str, Any]:
        """Stock ledger of one item: every voucher that moved it in or out, with quantity, rate, value
        and the running quantity. Also shows total inward / outward and the average purchase and sale rate."""
        start, end = tx.period(from_date, to_date)
        items = [r for r in core.list_masters("stock_item", company=company, from_date=start, to_date=end)
                 if r["name"].lower() == item.strip().lower()]
        if not items:
            raise TallyError(f"No stock item named '{item}'. Use tally_search with types=['stock_item'].")
        master = items[0]
        running = Decimal(str(master.get("opening_qty") or 0))
        lines: list[dict[str, Any]] = []
        totals = {"in_qty": Decimal(0), "in_value": Decimal(0), "out_qty": Decimal(0), "out_value": Decimal(0)}
        for voucher in core.fetch_vouchers(start, end, company=company):
            if voucher["cancelled"] or voucher["optional"]:
                continue
            for line in voucher.get("items", []):
                if line["item"].lower() != master["name"].lower():
                    continue
                qty, value = Decimal(str(line["qty"])), Decimal(str(line["amount"]))
                inward = line["direction"] == "in"
                running += qty if inward else -qty
                totals["in_qty" if inward else "out_qty"] += qty
                totals["in_value" if inward else "out_value"] += value
                lines.append({"date": voucher["date"], "type": voucher["type"], "number": voucher["number"],
                              "party": voucher["party"], "direction": line["direction"], "qty": float(qty),
                              "rate": line["rate"], "value": tx.money(value), "godown": line.get("godown", ""),
                              "balance_qty": float(running)})
        result: dict[str, Any] = {
            "item": master["name"], "unit": master.get("unit", ""), "from_date": start.isoformat(),
            "to_date": end.isoformat(), "opening_qty": master.get("opening_qty", 0),
            "inward_qty": float(totals["in_qty"]), "inward_value": tx.money(totals["in_value"]),
            "outward_qty": float(totals["out_qty"]), "outward_value": tx.money(totals["out_value"]),
            "closing_qty": float(running), "closing_qty_in_tally": master.get("closing_qty", 0),
            "closing_value_in_tally": master.get("closing_value", 0),
        }
        if totals["in_qty"]:
            result["average_inward_rate"] = tx.money(totals["in_value"] / totals["in_qty"])
        if totals["out_qty"]:
            result["average_outward_rate"] = tx.money(totals["out_value"] / totals["out_qty"])
        result.update(core.page(lines, limit, 0, "movements"))
        return result
