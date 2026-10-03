"""Pure functions: dates, amounts, GSTIN, XML parsing, XML building."""

import datetime as dt
from decimal import Decimal

import pytest

from rajtally_mcp import builders
from rajtally_mcp import tally_xml as tx
from rajtally_mcp.tools.gst import check_gstin


@pytest.mark.parametrize("text", ["2024-04-01", "01-04-2024", "01/04/2024", "20240401", "1-Apr-2024",
                                  "1-Apr-24", "01.04.2024"])
def test_dates_in_every_common_format(text):
    assert tx.parse_date(text) == dt.date(2024, 4, 1)
    assert tx.tally_date(text) == "20240401"


def test_bad_date_is_a_clear_error():
    with pytest.raises(tx.TallyError, match="Could not understand the date"):
        tx.parse_date("next tuesday")


def test_financial_year_and_period():
    assert tx.financial_year(dt.date(2025, 2, 10)) == (dt.date(2024, 4, 1), dt.date(2025, 3, 31))
    assert tx.financial_year(dt.date(2025, 4, 1)) == (dt.date(2025, 4, 1), dt.date(2026, 3, 31))
    assert tx.period("FY2024-25", None) == (dt.date(2024, 4, 1), dt.date(2025, 3, 31))
    assert tx.period("2024-06-01", "2024-06-30") == (dt.date(2024, 6, 1), dt.date(2024, 6, 30))
    assert tx.period("2024-06-01", None) == (dt.date(2024, 6, 1), dt.date(2025, 3, 31))
    with pytest.raises(tx.TallyError, match="before"):
        tx.period("2024-06-30", "2024-06-01")


@pytest.mark.parametrize("text,expected", [
    ("-5000.00", "-5000.00"), ("5,00,000.50", "500000.50"), ("", "0"), (None, "0"),
    ("1000.00 Dr", "-1000.00"), ("1000.00 Cr", "1000.00"), ("(-)250.00", "-250.00"),
    ("$ 100.00 @ ₹ 83.00/$ = ₹ 8300.00", "8300.00"), ("-$ 10 @ 83/$ = -830.00", "-830.00"),
])
def test_amounts(text, expected):
    assert tx.amount(text) == Decimal(expected)


def test_quantity_and_rate():
    assert tx.quantity(" 10 Nos") == (Decimal("10"), "Nos")
    assert tx.quantity(" 2.500 Kg = 2500 Gm") == (Decimal("2.500"), "Kg")
    assert tx.quantity("") == (Decimal(0), "")
    assert tx.rate("450.00/Nos") == (Decimal("450.00"), "Nos")


def test_dr_cr_split():
    assert tx.dr_cr(Decimal("-100")) == {"dr": 100.0, "cr": 0.0}
    assert tx.dr_cr(Decimal("75.5")) == {"dr": 0.0, "cr": 75.5}


def test_control_characters_from_tally_are_cleaned():
    root = tx.parse("<ENVELOPE><GROUP NAME='Capital'><PARENT>&#4; Primary</PARENT></GROUP></ENVELOPE>")
    assert root.find("GROUP/PARENT").text.strip() == "Primary"


def test_error_responses_become_plain_errors():
    with pytest.raises(tx.TallyError, match="Unknown Request"):
        tx.parse("<RESPONSE>Unknown Request, cannot be processed</RESPONSE>")
    with pytest.raises(tx.TallyError, match="Could not find Report"):
        tx.parse("<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER><BODY><DATA>"
                 "<LINEERROR>Could not find Report 'X'!</LINEERROR></DATA></BODY></ENVELOPE>")
    with pytest.raises(tx.TallyError, match="empty response"):
        tx.parse("   ")


def test_decode_detects_utf16_with_and_without_bom():
    text = "<A>ગુજરાતી</A>"
    assert tx.decode(text.encode("utf-16")) == text
    assert tx.decode(text.encode("utf-16-le")) == text
    assert tx.decode(text.encode("utf-8")) == text


def test_flat_rows_pairs_names_with_amounts():
    root = tx.parse("<ENVELOPE><DSPACCNAME><DSPDISPNAME>Cash</DSPDISPNAME></DSPACCNAME>"
                    "<DSPACCINFO><DSPCLDRAMT><DSPCLDRAMTA>-100.00</DSPCLDRAMTA></DSPCLDRAMT></DSPACCINFO>"
                    "<DSPACCNAME><DSPDISPNAME>Bank</DSPDISPNAME></DSPACCNAME>"
                    "<DSPACCINFO><DSPCLDRAMT><DSPCLDRAMTA>-200.00</DSPCLDRAMTA></DSPCLDRAMT></DSPACCINFO>"
                    "</ENVELOPE>")
    assert tx.flat_rows(root) == [{"DSPDISPNAME": "Cash", "DSPCLDRAMTA": "-100.00"},
                                  {"DSPDISPNAME": "Bank", "DSPCLDRAMTA": "-200.00"}]


def test_import_result_reads_old_and_new_formats():
    new = ("<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DATA><IMPORTRESULT>"
           "<CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS><LASTVCHID>42</LASTVCHID>"
           "</IMPORTRESULT></DATA></BODY></ENVELOPE>")
    assert tx.import_result(new) == {"created": 1, "altered": 0, "errors": 0, "last_voucher_id": "42", "ok": True}
    old = "<RESPONSE><CREATED>0</CREATED><ERRORS>1</ERRORS><LINEERROR>Ledger 'X' does not exist!</LINEERROR></RESPONSE>"
    result = tx.import_result(old)
    assert result["ok"] is False and result["line_errors"] == ["Ledger 'X' does not exist!"]
    nothing = "<RESPONSE><CREATED>0</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></RESPONSE>"
    assert tx.import_result(nothing)["ok"] is False and "changed nothing" in tx.import_result(nothing)["note"]


def test_to_xml_handles_nested_lists_and_escaping():
    xml = tx.to_xml("ledger", {"parent": "A & B", "ADDRESS.LIST": {"ADDRESS": ["l1", "l2"]}, "isbillwiseon": True})
    assert xml == ("<LEDGER><PARENT>A &amp; B</PARENT><ADDRESS.LIST><ADDRESS>l1</ADDRESS><ADDRESS>l2</ADDRESS>"
                   "</ADDRESS.LIST><ISBILLWISEON>Yes</ISBILLWISEON></LEDGER>")


def test_gstin_check_digit():
    assert check_gstin("27AAPFU0939F1ZV")["valid"] is True
    assert check_gstin("27aapfu0939f1zv")["state"] == "Maharashtra"
    assert check_gstin("27AAPFU0939F1ZV")["pan"] == "AAPFU0939F"
    wrong = check_gstin("27AAPFU0939F1ZX")
    assert wrong["valid"] is False and "'V'" in wrong["problem"]
    assert "15 characters" in check_gstin("27AAPFU0939F1Z")["problem"]
    assert "not a GST state code" in check_gstin("00AAPFU0939F1ZV")["problem"]


def test_voucher_must_balance():
    with pytest.raises(tx.TallyError, match="does not balance.*difference 100.00"):
        builders.accounting_voucher_xml("Journal", "2024-04-01", [{"ledger": "A", "dr": 1000},
                                                                  {"ledger": "B", "cr": 900}])
    with pytest.raises(tx.TallyError, match="both dr and cr"):
        builders.accounting_voucher_xml("Journal", "2024-04-01", [{"ledger": "A", "dr": 10, "cr": 10},
                                                                  {"ledger": "B", "cr": 0}])


def test_voucher_xml_uses_tally_sign_convention():
    xml = builders.accounting_voucher_xml("Payment", "2024-04-05", [
        {"ledger": "Rent", "dr": 15000}, {"ledger": "Cash", "amount": 15000, "type": "Cr"}], narration="R & D")
    assert "<DATE>20240405</DATE>" in xml and "<NARRATION>R &amp; D</NARRATION>" in xml
    assert ("<LEDGERNAME>Rent</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-15000.00</AMOUNT>"
            in xml)
    assert ("<LEDGERNAME>Cash</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>15000.00</AMOUNT>"
            in xml)
    assert "VOUCHERNUMBER" not in xml  # blank number = Tally numbers it


def test_invoice_totals_local_interstate_and_round_off():
    items = [{"item": "Valve", "qty": 3, "unit": "Nos", "rate": 333.33, "gst_rate": 18}]
    gst = {"cgst_ledger": "CGST", "sgst_ledger": "SGST", "igst_ledger": "IGST"}
    local = builders.invoice_xml("sales", "2024-04-01", "Party", items, ledger="Sales", gst=gst,
                                 round_off_ledger="Round Off")
    assert local["summary"] == {
        "voucher_type": "Sales", "date": "2024-04-01", "party": "Party", "taxable_value": 999.99, "tax": 180.0,
        "tax_lines": [{"ledger": "CGST", "amount": 90.0}, {"ledger": "SGST", "amount": 90.0}],
        "other_charges": 0.0, "round_off": 0.01, "invoice_total": 1180.0}
    # sales: party debited (negative), everything else credited (positive)
    assert "<LEDGERNAME>Party</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>-1180.00</AMOUNT>" in local["xml"]
    assert "<LEDGERNAME>Round Off</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>0.01</AMOUNT>" in local["xml"]
    inter = builders.invoice_xml("purchase", "2024-04-01", "Party", items, ledger="Purchase",
                                 gst={**gst, "interstate": True})
    assert inter["summary"]["tax_lines"] == [{"ledger": "IGST", "amount": 180.0}]
    # purchase: party credited (positive), stock and tax debited (negative)
    assert "<LEDGERNAME>Party</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>1179.99</AMOUNT>" in inter["xml"]
    assert "<LEDGERNAME>IGST</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-180.00</AMOUNT>" in inter["xml"]


def test_invoice_xml_sums_to_zero():
    """Whatever the mix of items, tax, charges and rounding, the voucher XML must balance."""
    import re
    built = builders.invoice_xml(
        "sales", "2024-04-01", "Party",
        [{"item": "A", "qty": 7, "unit": "Nos", "rate": 123.45, "gst_rate": 12, "discount_percent": 2.5},
         {"item": "B", "qty": 1.5, "unit": "Kg", "rate": 999.99, "gst_rate": 28}],
        ledger="Sales", gst={"cgst_ledger": "C", "sgst_ledger": "S"},
        charges=[{"ledger": "Freight", "amount": 75.25}], round_off_ledger="Round Off")
    ledger_lines = re.findall(r"<LEDGERENTRIES\.LIST>.*?<AMOUNT>(-?[\d.]+)</AMOUNT>", built["xml"])
    allocations = re.findall(r"<ACCOUNTINGALLOCATIONS\.LIST>.*?<AMOUNT>(-?[\d.]+)</AMOUNT>", built["xml"])
    assert sum(Decimal(a) for a in ledger_lines + allocations) == 0
