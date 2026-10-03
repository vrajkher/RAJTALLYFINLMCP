"""Every tool, called through the MCP protocol against the pretend Tally."""

import re

import pytest

from rajtally_mcp import config

from conftest import ToolFailed


# ---------------------------------------------------------------- connect

def test_tool_list_is_complete_and_described(call):
    tools = {t.name: t for t in call.tools()}
    assert len(tools) == 53
    for name, tool in tools.items():
        assert tool.description and len(tool.description) > 30, name
        assert re.fullmatch(r"[a-z0-9_]+", name), name  # safe for every MCP client
    readers = [n for n, t in tools.items() if t.annotations and t.annotations.read_only_hint]
    assert "tally_trial_balance" in readers and "tally_create_voucher" not in readers
    assert tools["tally_delete_voucher"].annotations.destructive_hint is True


def test_status_and_company(call):
    status = call("tally_status")
    assert status["connected"] is True and status["product"] == "TallyPrime"
    assert status["active_company"] == "Demo Traders Pvt Ltd" and "problem" not in status
    info = call("tally_company_info")
    assert info["state"] == "Gujarat" and info["features"]["gst"] is True
    picked = call("tally_use_company", name="demo traders pvt ltd")
    assert picked["selected_company"] == "Demo Traders Pvt Ltd"
    with pytest.raises(ToolFailed, match="is not open in Tally"):
        call("tally_use_company", name="Some Other Co")


def test_status_explains_when_tally_is_not_running(call, monkeypatch):
    config.update({"port": 1}, persist=False)
    status = call("tally_status")
    assert status["connected"] is False and "Cannot reach Tally" in status["problem"]
    with pytest.raises(ToolFailed, match="Cannot reach Tally.*HTTP server"):
        call("tally_trial_balance")


def test_guide_lists_only_real_tools(call):
    guide = call("tally_guide")["result"]
    names = {t.name for t in call.tools()}
    mentioned = set(re.findall(r"\btally_[a-z0-9_]+", guide))
    assert mentioned - names == set()
    assert names - mentioned == {"search_mentions", "tally_guide"}


# ---------------------------------------------------------------- read

def test_masters_read(call):
    debtors = call("tally_list_masters", master_type="ledger", parent="Sundry Debtors")
    assert [m["name"] for m in debtors["masters"]] == ["Patel Engineering Works", "Shree Ganesh Hardware",
                                                       "Walk-in Customer"]
    patel = debtors["masters"][0]
    assert patel["closing_balance"] == 2714.0 and patel["closing_balance_type"] == "Dr"
    assert patel["opening_balance"] == 0.0 and patel["state"] == "Maharashtra"
    assets = call("tally_list_masters", master_type="ledger", parent="Current Assets")
    assert assets["total"] == 5  # cash + bank + 3 debtors, through the sub-groups
    paged = call("tally_list_masters", master_type="group", limit=5)
    assert paged["returned"] == 5 and "offset=5" in paged["more"]
    hits = call("tally_search", text="valve")
    assert [h["name"] for h in hits["matches"]] == ["Ball Valve 2 inch", "Gate Valve 4 inch"]
    one = call("tally_get_master", master_type="ledger", name="shree ganesh hardware")
    assert one["summary"]["gstin"].startswith("24AABCS1234K") and one["all_fields"]["PARENT"] == "Sundry Debtors"
    with pytest.raises(ToolFailed, match="Unknown master type"):
        call("tally_list_masters", master_type="widgets")
    with pytest.raises(ToolFailed, match="No ledger named"):
        call("tally_get_master", master_type="ledger", name="Nobody")


def test_vouchers_read(call):
    book = call("tally_list_vouchers")
    assert book["total"] == 9 and "entries" not in book["vouchers"][0]
    sales = call("tally_list_vouchers", voucher_type="Sales", include_entries=True)
    assert [v["number"] for v in sales["vouchers"]] == ["S-001", "S-002", "S-003"]
    assert sales["total_value"] == 11564.0
    assert call("tally_list_vouchers", ledger="Rent")["total"] == 1
    assert call("tally_list_vouchers", search="cheque")["total"] == 1
    assert call("tally_list_vouchers", min_amount=50000)["total"] == 1
    assert call("tally_list_vouchers", source="daybook")["total"] == 9
    first_week = call("tally_list_vouchers", from_date=book["from_date"],
                      to_date=book["vouchers"][1]["date"])
    assert first_week["total"] == 2
    voucher = call("tally_get_voucher", number="S-001", voucher_type="Sales")
    assert voucher["amount"] == 5310.0 and voucher["items"][0]["qty"] == 10.0
    assert {e["ledger"]: (e["dr"], e["cr"]) for e in voucher["entries"]} == {
        "Shree Ganesh Hardware": (5310.0, 0.0), "CGST": (0.0, 405.0), "SGST": (0.0, 405.0),
        "Sales @ 18%": (0.0, 4500.0)}
    assert call("tally_get_voucher", master_id=voucher["master_id"])["number"] == "S-001"
    with pytest.raises(ToolFailed, match="No voucher matched"):
        call("tally_get_voucher", number="S-999", voucher_type="Sales")


# ---------------------------------------------------------------- reports

def test_financial_statements_tie_out(call):
    trial = call("tally_trial_balance")
    assert trial["difference"] == 0.0 and trial["total_closing_dr"] == trial["total_closing_cr"] == 609314.0
    by_head = call("tally_trial_balance", level="primary")
    assert {r["name"] for r in by_head["rows"]} >= {"Current Assets", "Capital Account", "Sales Accounts"}
    pnl = call("tally_profit_loss", detailed=True)
    # sales 9800 + closing stock 44100 - opening stock 6000 - purchases 45000 - freight 20000
    assert pnl["gross_profit"] == -17100.0 and pnl["net_profit"] == -32100.0
    sheet = call("tally_balance_sheet")
    assert sheet["difference"] == 0.0 and sheet["total_assets"] == sheet["total_liabilities"] == 560664.0
    assert sheet["net_profit"] == pnl["net_profit"]


def test_ledger_statement_runs_to_the_tally_balance(call):
    statement = call("tally_ledger_statement", ledger="hdfc bank")
    assert statement["opening_balance"] == 500000.0 and statement["closing_balance"] == 485310.0
    assert [t["balance"] for t in statement["transactions"]] == [505310.0, 475310.0, 485310.0]
    assert "warning" not in statement
    party = call("tally_ledger_statement", ledger="Shree Ganesh Hardware")
    assert party["total_dr"] == party["total_cr"] == 5310.0 and party["closing_balance"] == 0.0


def test_outstandings_and_cash(call):
    due = call("tally_outstandings")
    assert due["total_pending"] == 6254.0 and {b["bill"] for b in due["bills"]} == {"S-002", "S-003"}
    owed = call("tally_outstandings", kind="payable", party="Ambika Steel Suppliers")
    assert owed["total_pending"] == 23100.0 and owed["bills"][0]["bill"] == "ASS/101"
    cash = call("tally_cash_bank_balances")
    assert cash["net_cash_and_bank"] == 510310.0
    summary = call("tally_group_summary", group="sundry debtors")
    assert summary["closing_balance"] == 6254.0 and summary["closing_type"] == "Dr"
    months = call("tally_monthly_summary", group="Sales Accounts")
    assert months["total_cr"] == 9800.0 and len(months["months"]) == 1
    native = call("tally_native_report", report="Trial Balance")
    assert native["total"] == 15 and "DSPDISPNAME" in native["rows"][0]
    with pytest.raises(ToolFailed, match="Could not find Report"):
        call("tally_native_report", report="No Such Report")


def test_inventory(call):
    stock = call("tally_stock_summary")
    assert stock["closing_stock_value"] == 44100.0
    valve = next(i for i in stock["items"] if i["name"] == "Ball Valve 2 inch")
    assert valve["closing_qty"] == 105.0 and valve["hsn"] == "848180" and valve["gst_rate"] == 18.0
    moves = call("tally_stock_movement", item="ball valve 2 inch")
    assert moves["closing_qty"] == moves["closing_qty_in_tally"] == 105.0
    assert moves["inward_qty"] == 100.0 and moves["outward_qty"] == 15.0


def test_gst_reports(call):
    register = call("tally_register", kind="sales")
    assert register["totals"] == {"taxable_value": 9800.0, "cgst": 675.0, "sgst": 675.0, "igst": 414.0,
                                  "cess": 0.0, "total": 11564.0}
    purchases = call("tally_register", kind="purchase")
    assert purchases["totals"]["taxable_value"] == 45000.0 and purchases["invoices"][0]["reference"] == "ASS/101"
    gst = call("tally_gst_summary")
    assert gst["total_output_tax"] == 1764.0 and gst["total_input_tax_credit"] == 8100.0
    assert gst["net_gst_payable"] == -6336.0
    gstr1 = call("tally_gstr1")
    assert gstr1["b2b"]["count"] == 2 and gstr1["b2c"]["count"] == 1 and gstr1["needs_attention"] == []
    hsn = call("tally_hsn_summary")
    assert {r["hsn"]: r["taxable_value"] for r in hsn["rows"]} == {"7214": 3000.0, "848180": 6800.0}
    assert call("tally_validate_gstin", gstin="24AAACC1206D1ZM")["state"] == "Gujarat"


def test_compliance(call):
    tds = call("tally_tds_summary")
    assert tds["total_deducted"] == 400.0 and tds["deductees"][0]["pan"] == "ABCPM1234F"
    bank = call("tally_bank_reconciliation", bank_ledger="HDFC Bank")
    assert bank["balance_as_per_books"] == 485310.0 and bank["add_payments_not_presented"] == 30000.0
    audit = call("tally_audit_checks")
    found = {f["check"]: f["found"] for f in audit["findings"]}
    assert found["Cash payments above Rs 10,000"] == 1 and found["Days with negative cash balance"] == 0
    health = call("tally_data_health")
    assert health["parties_without_gstin"]["ledgers"] == ["Mahavir Transport", "Walk-in Customer"]
    assert health["invalid_gstin"] == []


def test_sql_and_tdl(call):
    rows = call("tally_sql", sql="SELECT $Name, $ClosingBalance FROM Ledger WHERE $Parent = 'Sundry Debtors' "
                                 "ORDER BY $Name")
    assert rows["via"] == "xml" and [r["$Name"] for r in rows["rows"]][0] == "Patel Engineering Works"
    like = call("tally_sql", sql="SELECT $Name FROM Ledger WHERE $Name LIKE '%bank%'")
    assert [r["$Name"] for r in like["rows"]] == ["HDFC Bank"]
    for bad in ("DELETE FROM Ledger", "SELECT $Name FROM Ledger; DROP TABLE x"):
        with pytest.raises(ToolFailed, match="Only a single SELECT"):
            call("tally_sql", sql=bad)
    with pytest.raises(ToolFailed, match="pyodbc"):
        call("tally_sql", sql="SELECT $Name FROM Ledger", via="odbc")
    big = call("tally_tdl_collection", object_type="Ledger", fields=["Name", "ClosingBalance"],
               filters={"Big": "$ClosingBalance < -100000"})
    assert [r["NAME"] for r in big["rows"]] == ["HDFC Bank"]
    assert call("tally_evaluate", function="LicenseInfo", params=["SerialNumber"])["result"] == "700000001"
    assert "Voucher" in call("tally_odbc_tables")["tables"]


# ---------------------------------------------------------------- write

def test_create_ledger_dry_run_then_real(call, tally):
    args = dict(name="Krishna Traders", group="Sundry Debtors", gstin="24AAACC1206D1ZM", state="Gujarat",
                opening_balance=2500, opening_type="Dr", bill_wise=True, address=["CG Road", "Ahmedabad"])
    preview = call("tally_create_ledger", dry_run=True, **args)
    assert preview["nothing_changed"] is True and "<PARTYGSTIN>24AAACC1206D1ZM</PARTYGSTIN>" in preview["xml"]
    assert "<LEDGSTREGDETAILS.LIST>" in preview["xml"] and "<OPENINGBALANCE>-2500.00</OPENINGBALANCE>" in preview["xml"]
    assert call("tally_search", text="krishna")["total"] == 0
    assert call("tally_create_ledger", **args) == {"created": 1, "altered": 0, "deleted": 0, "cancelled": 0,
                                                    "combined": 0, "ignored": 0, "errors": 0, "exceptions": 0,
                                                    "ok": True}
    saved = call("tally_get_master", master_type="ledger", name="Krishna Traders")["summary"]
    assert saved["gstin"] == "24AAACC1206D1ZM" and saved["opening_balance"] == 2500.0
    with pytest.raises(ToolFailed, match="GSTIN .* looks wrong"):
        call("tally_create_ledger", name="Bad", group="Sundry Debtors", gstin="24AAACC1206D1ZX")
    failed = call("tally_create_ledger", name="Orphan", group="No Such Group")
    assert failed["ok"] is False and failed["line_errors"] == ["Group 'No Such Group' does not exist!"]


def test_erp9_schema_omits_prime_only_tags(call):
    call("tally_settings_update", set={"gstSchema": "erp9"})
    xml = call("tally_create_ledger", name="Old Style", group="Sundry Debtors", gstin="24AAACC1206D1ZM",
               dry_run=True)["xml"]
    assert "<PARTYGSTIN>" in xml and "LEDGSTREGDETAILS" not in xml
    item = call("tally_create_stock_item", name="Widget", unit="Nos", hsn="8481", gst_rate=18, dry_run=True)["xml"]
    assert "<GSTRATEDUTYHEAD>Central Tax</GSTRATEDUTYHEAD>" in item and "<HSNCODE>8481</HSNCODE>" in item


def test_other_masters(call):
    assert call("tally_create_group", name="Export Debtors", parent="Sundry Debtors")["created"] == 1
    assert call("tally_save_master", master_type="unit", name="Box",
                fields={"ISSIMPLEUNIT": "Yes", "ORIGINALNAME": "Boxes"})["created"] == 1
    item = call("tally_create_stock_item", name="Check Valve", unit="Box", stock_group="Valves", hsn="848130",
                gst_rate=12, opening_qty=4, opening_rate=250)
    assert item["created"] == 1
    saved = call("tally_get_master", master_type="stock_item", name="Check Valve")["summary"]
    assert saved["gst_rate"] == 12.0 and saved["hsn"] == "848130" and saved["closing_qty"] == 4.0
    missing_unit = call("tally_create_stock_item", name="X", unit="Dozen")
    assert missing_unit["ok"] is False and "Unit 'Dozen' does not exist" in missing_unit["line_errors"][0]
    altered = call("tally_save_master", master_type="ledger", name="Rent", action="alter",
                   fields={"PARENT": "Direct Expenses"})
    assert altered["altered"] == 1
    assert call("tally_get_master", master_type="ledger", name="Rent")["summary"]["parent"] == "Direct Expenses"
    assert call("tally_rename_master", master_type="ledger", name="Rent", new_name="Shop Rent")["altered"] == 1
    assert call("tally_list_vouchers", ledger="Shop Rent")["total"] == 1


def test_create_voucher_and_see_it_in_reports(call):
    entries = [{"ledger": "Rent", "dr": 8000, "cost_centres": [{"name": "Head Office", "amount": 8000}]},
               {"ledger": "HDFC Bank", "cr": 8000,
                "bank": {"instrument_number": "000777", "favouring": "Landlord", "bank_date": "2026-05-12"}}]
    preview = call("tally_create_voucher", voucher_type="Payment", date="2026-05-10", entries=entries,
                   narration="May rent", dry_run=True)
    assert preview["summary"]["amount"] == 8000.0 and "<INSTRUMENTNUMBER>000777</INSTRUMENTNUMBER>" in preview["xml"]
    done = call("tally_create_voucher", voucher_type="Payment", date="2026-05-10", entries=entries,
                narration="May rent")
    assert done["ok"] is True and done["created"] == 1
    voucher = call("tally_get_voucher", master_id=done["master_id"])
    assert voucher["narration"] == "May rent" and voucher["entries"][1]["bank"][0]["bank_date"] == "2026-05-12"
    assert voucher["entries"][0]["cost_centres"][0]["cost_centre"] == "Head Office"
    assert call("tally_trial_balance")["difference"] == 0.0
    assert call("tally_bank_reconciliation", bank_ledger="HDFC Bank")["cleared_entries"] == 1
    unbalanced = [{"ledger": "Rent", "dr": 100}, {"ledger": "Cash", "cr": 90}]
    with pytest.raises(ToolFailed, match="does not balance"):
        call("tally_create_voucher", voucher_type="Payment", date="2026-05-10", entries=unbalanced)
    bad = call("tally_create_voucher", voucher_type="Payment", date="2026-05-10",
               entries=[{"ledger": "Unknown Ledger", "dr": 100}, {"ledger": "Cash", "cr": 100}])
    assert bad["ok"] is False and bad["line_errors"] == ["Ledger 'Unknown Ledger' does not exist!"]


def test_create_invoice_updates_stock_gst_and_outstandings(call):
    done = call("tally_create_invoice", kind="sales", date="2026-06-01", party="Shree Ganesh Hardware",
                ledger="Sales @ 18%", number="S-004", round_off_ledger="Round Off",
                gst={"cgst_ledger": "CGST", "sgst_ledger": "SGST"},
                items=[{"item": "Ball Valve 2 inch", "qty": 4, "unit": "Nos", "rate": 455.55, "gst_rate": 18}])
    assert done["ok"] is True
    assert done["summary"]["taxable_value"] == 1822.2 and done["summary"]["invoice_total"] == 2150.0
    assert call("tally_stock_movement", item="Ball Valve 2 inch")["closing_qty"] == 101.0
    assert call("tally_outstandings", party="Shree Ganesh Hardware")["total_pending"] == 2150.0
    assert call("tally_register", kind="sales")["totals"]["taxable_value"] == 11622.2
    assert call("tally_balance_sheet")["difference"] == 0.0
    note = call("tally_create_invoice", kind="credit_note", date="2026-06-03", party="Shree Ganesh Hardware",
                ledger="Sales @ 18%", gst={"cgst_ledger": "CGST", "sgst_ledger": "SGST"}, bill_name="S-004",
                bill_type="Agst Ref",
                items=[{"item": "Ball Valve 2 inch", "qty": 1, "unit": "Nos", "rate": 455.55, "gst_rate": 18}])
    assert note["ok"] is True
    assert call("tally_stock_movement", item="Ball Valve 2 inch")["closing_qty"] == 102.0
    assert call("tally_register", kind="credit_note")["totals"]["taxable_value"] == 455.55
    assert call("tally_gst_summary")["total_output_tax"] == 1764.0 + 328.0 - 82.0
    with pytest.raises(ToolFailed, match="kind must be one of"):
        call("tally_create_invoice", kind="quotation", date="2026-06-01", party="X", ledger="Y", items=[])


def test_stock_journal_moves_stock(call):
    done = call("tally_create_stock_journal", date="2026-06-05",
                consumed=[{"item": "Steel Rod", "qty": 10, "unit": "Kg", "rate": 60}],
                produced=[{"item": "Gate Valve 4 inch", "qty": 2, "unit": "Nos", "rate": 300}])
    assert done["ok"] is True
    stock = {i["name"]: i["closing_qty"] for i in call("tally_stock_summary")["items"]}
    assert stock["Steel Rod"] == 200.0 and stock["Gate Valve 4 inch"] == 2.0


def test_alter_voucher(call):
    target = call("tally_list_vouchers", voucher_type="Payment", ledger="Rent")["vouchers"][0]
    changed = call("tally_alter_voucher", master_id=target["master_id"], new_narration="Rent for April",
                   new_date="2026-04-20",
                   new_entries=[{"ledger": "Rent", "dr": 12000}, {"ledger": "Cash", "cr": 12000}])
    assert changed["altered"] == 1
    now = changed["voucher_now"]
    assert now["narration"] == "Rent for April" and now["date"] == "2026-04-20" and now["amount"] == 12000.0
    assert now["master_id"] == target["master_id"]
    assert call("tally_trial_balance")["difference"] == 0.0
    invoice = call("tally_list_vouchers", voucher_type="Sales")["vouchers"][0]
    kept = call("tally_alter_voucher", master_id=invoice["master_id"], new_narration="Edited")["voucher_now"]
    assert kept["narration"] == "Edited" and kept["items"][0]["qty"] == 10.0 and kept["amount"] == 5310.0
    with pytest.raises(ToolFailed, match="has item lines"):
        call("tally_alter_voucher", master_id=invoice["master_id"],
             new_entries=[{"ledger": "Cash", "dr": 1}, {"ledger": "Rent", "cr": 1}])


def test_delete_and_cancel_need_full_access_and_confirmation(call):
    target = call("tally_list_vouchers", voucher_type="Contra")["vouchers"][0]
    with pytest.raises(ToolFailed, match="need access = 'full'"):
        call("tally_delete_voucher", master_id=target["master_id"], confirm=True)
    call("tally_settings_update", set={"access": "full"})
    with pytest.raises(ToolFailed, match="confirm=true"):
        call("tally_delete_voucher", master_id=target["master_id"])
    preview = call("tally_delete_voucher", master_id=target["master_id"], dry_run=True)
    assert preview["nothing_changed"] is True and 'ACTION="Delete"' in preview["xml"]
    assert call("tally_list_vouchers")["total"] == 9
    assert call("tally_delete_voucher", master_id=target["master_id"], confirm=True)["deleted"] == 1
    assert call("tally_list_vouchers")["total"] == 8
    sale = call("tally_list_vouchers", voucher_type="Sales")["vouchers"][2]
    assert call("tally_cancel_voucher", master_id=sale["master_id"], reason="Wrong party",
                confirm=True)["cancelled"] == 1
    assert call("tally_get_voucher", master_id=sale["master_id"])["cancelled"] is True
    assert call("tally_register", kind="sales")["totals"]["taxable_value"] == 6800.0
    assert call("tally_balance_sheet")["difference"] == 0.0
    in_use = call("tally_delete_master", master_type="ledger", name="Cash", confirm=True)
    assert in_use["ok"] is False and "used in vouchers" in in_use["line_errors"][0]
    assert call("tally_delete_master", master_type="stock_item", name="Gate Valve 4 inch",
                confirm=True)["deleted"] == 1


def test_read_only_mode_blocks_every_write(call, tally):
    call("tally_settings_update", set={"access": "read_only"})
    before = len(tally.requests)
    attempts = [
        ("tally_create_ledger", dict(name="A", group="Sundry Debtors")),
        ("tally_create_group", dict(name="A", parent="Sundry Debtors")),
        ("tally_create_stock_item", dict(name="A", unit="Nos")),
        ("tally_save_master", dict(master_type="godown", name="A")),
        ("tally_rename_master", dict(master_type="ledger", name="Cash", new_name="B")),
        ("tally_create_voucher", dict(voucher_type="Journal", date="2026-05-01",
                                      entries=[{"ledger": "Rent", "dr": 1}, {"ledger": "Cash", "cr": 1}])),
        ("tally_create_invoice", dict(kind="sales", date="2026-05-01", party="Walk-in Customer",
                                      ledger="Sales @ 18%", items=[{"item": "Steel Rod", "qty": 1, "unit": "Kg",
                                                                    "rate": 10}])),
        ("tally_create_stock_journal", dict(date="2026-05-01",
                                            consumed=[{"item": "Steel Rod", "qty": 1, "unit": "Kg", "rate": 1}])),
        ("tally_bulk_import", dict(ledgers=[{"name": "A", "group": "Sundry Debtors"}])),
        ("tally_import_xml", dict(xml="<LEDGER NAME='A' ACTION='Create'/>", kind="masters")),
        ("tally_alter_voucher", dict(number="S-001", voucher_type="Sales", new_narration="x")),
    ]
    for tool, arguments in attempts:
        with pytest.raises(ToolFailed, match="read_only mode"):
            call(tool, **arguments)
    envelope = ("<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Import</TALLYREQUEST><TYPE>Data</TYPE>"
                "<ID>All Masters</ID></HEADER><BODY><DATA><TALLYMESSAGE><LEDGER NAME='A' ACTION='Create'/>"
                "</TALLYMESSAGE></DATA></BODY></ENVELOPE>")
    with pytest.raises(ToolFailed, match="read_only mode"):
        call("tally_raw_xml", xml=envelope, confirm_write=True)
    for tool in ("tally_delete_voucher", "tally_cancel_voucher"):
        with pytest.raises(ToolFailed, match="need access = 'full'"):
            call(tool, number="S-001", voucher_type="Sales", confirm=True)
    with pytest.raises(ToolFailed, match="need access = 'full'"):
        call("tally_delete_master", master_type="ledger", name="Rent", confirm=True)
    assert not any("<TALLYREQUEST>Import" in r for r in tally.requests[before:])
    assert call("tally_list_vouchers")["total"] == 9


def test_bulk_import_reports_each_failure(call):
    result = call("tally_bulk_import",
                  ledgers=[{"name": "Bulk Customer", "group": "Sundry Debtors", "state": "Gujarat"},
                           {"name": "Broken", "group": "Nowhere"}],
                  vouchers=[{"voucher_type": "Receipt", "date": "2026-05-02", "narration": "Advance",
                             "entries": [{"ledger": "Cash", "dr": 500}, {"ledger": "Bulk Customer", "cr": 500}]},
                            {"voucher_type": "Receipt", "date": "2026-05-02",
                             "entries": [{"ledger": "Cash", "dr": 500}, {"ledger": "Bulk Customer", "cr": 400}]},
                            {"voucher_type": "Receipt", "entries": []}])
    assert result["posted"] == 2 and result["failed"] == 3
    assert [f["record"] for f in result["failures"]] == ["ledger #2", "voucher #2", "voucher #3"]
    assert "Group 'Nowhere' does not exist" in result["failures"][0]["problem"]
    assert call("tally_ledger_statement", ledger="Bulk Customer")["closing_balance"] == 500.0


def test_raw_xml_and_import_xml(call):
    export = ("<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE>"
              "<ID>Trial Balance</ID></HEADER><BODY><DESC><STATICVARIABLES/></DESC></BODY></ENVELOPE>")
    assert "<DSPDISPNAME>Cash</DSPDISPNAME>" in call("tally_raw_xml", xml=export)["response"]
    envelope = ("<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Import</TALLYREQUEST><TYPE>Data</TYPE>"
                "<ID>All Masters</ID></HEADER><BODY><DATA><TALLYMESSAGE><GODOWN NAME='Shop' ACTION='Create'/>"
                "</TALLYMESSAGE></DATA></BODY></ENVELOPE>")
    with pytest.raises(ToolFailed, match="confirm_write=true"):
        call("tally_raw_xml", xml=envelope)
    assert call("tally_raw_xml", xml=envelope, confirm_write=True)["import_result"]["created"] == 1
    assert call("tally_import_xml", kind="masters",
                xml="<COSTCENTRE NAME='Branch' ACTION='Create'><NAME>Branch</NAME></COSTCENTRE>")["created"] == 1
    with pytest.raises(ToolFailed, match="not a full <ENVELOPE>"):
        call("tally_import_xml", xml=envelope)
    with pytest.raises(ToolFailed, match="need access = 'full'"):
        call("tally_import_xml", kind="masters", xml='<GODOWN NAME="Shop" ACTION="Delete"/>')


def test_gujarati_names_survive_the_round_trip(call):
    name = "શ્રી અંબિકા ટ્રેડર્સ"
    assert call("tally_create_ledger", name=name, group="Sundry Creditors")["created"] == 1
    assert call("tally_search", text="અંબિકા")["matches"][0]["name"] == name
    call("tally_create_voucher", voucher_type="Journal", date="2026-05-05", narration="ભાડું",
         entries=[{"ledger": "Rent", "dr": 700}, {"ledger": name, "cr": 700}])
    assert call("tally_ledger_statement", ledger=name)["transactions"][0]["narration"] == "ભાડું"


# ---------------------------------------------------------------- OpenAI extensions

def test_settings_extension(call, tmp_path):
    read = call("tally_settings_read")
    assert read["values"]["access"] == "read_write" and read["values"]["host"] == "127.0.0.1"
    assert set(read["schema"]["properties"]) == {"host", "port", "company", "access", "odbcDsn", "timeout",
                                                 "gstSchema"}
    assert [g["title"] for g in read["layout"]] == ["Connection", "Safety", "Advanced"]
    updated = call("tally_settings_update", set={"timeout": 15, "access": "full"})
    assert updated["values"]["timeout"] == 15 and updated["values"]["access"] == "full"
    assert '"timeout": 15' in (tmp_path / "settings.json").read_text()
    with pytest.raises(ToolFailed):
        call("tally_settings_update", set={"access": "everything"})


def test_mentions_extension(call):
    found = call("search_mentions", query="patel")
    assert found["items"][0]["name"] == "Patel Engineering Works"
    assert found["items"][0]["uri"] == "tally://ledger/Patel%20Engineering%20Works"
    tool = next(t for t in call.tools() if t.name == "search_mentions")
    assert tool.meta["openai/extensions"] == {"mentions/search": {}}
