# Tool reference

All 53 tools, in the order you would use them. Generated from the server itself by `scripts/make_tool_docs.py`.

Every tool also accepts `company` to work on a company other than the selected one, and every write tool accepts `dry_run` to preview without changing anything.

## Step 1 - Connect

### `tally_guide`  (read)

START HERE. The step-by-step map of every tool in this server and the rules for using them.

### `tally_status`  (read)

Check the connection to Tally: is it running, which product, which companies are open, which company is selected, and whether writing is allowed. Run this first if anything fails.

### `tally_list_companies`  (read)

List the companies currently open in Tally, with their financial-year and books-from dates.

### `tally_use_company`  (settings)

Choose the company every other tool works on. Pass the exact name from tally_list_companies, or '' to follow whichever company is active in Tally. remember=true saves the choice.

Arguments: `name`, `remember`?  (`?` = optional)

### `tally_company_info`  (read)

Company profile: name, address, state, GSTIN, PAN, financial year, and which features (inventory, bill-wise, cost centres, GST, TDS, payroll) are switched on. all_fields=true returns every stored field.

Arguments: `all_fields`?  (`?` = optional)

### `tally_settings_read`  (read)

Show the connection settings: Tally host and port, selected company, access level (read_only / read_write / full), ODBC data source, timeout and Tally version.

### `tally_settings_update`  (settings)

Change connection settings and save them. Pass only what should change inside 'set', e.g. {"set": {"access": "full"}} to allow delete and cancel, {"set": {"port": 9001}}, {"set": {"gstSchema": "erp9"}} for Tally.ERP 9. Keys: host, port, company, access, odbcDsn, timeout, gstSchema.

Arguments: `set`  (`?` = optional)

## Step 2 - Look things up

### `tally_search`  (read)

Find masters by part of their name. Use this before any tool that needs an exact Tally name. types defaults to ledger, group and stock_item; any master type is allowed.

Arguments: `text`, `types`?, `limit`?  (`?` = optional)

### `tally_list_masters`  (read)

List masters of one type with their key fields (balances for ledgers/groups, quantity and value for stock items).

```
master_type: ledger, group, stock_item, stock_group, stock_category, unit, godown, cost_centre,
cost_category, voucher_type, currency, budget, employee, employee_group, pay_head, attendance_type.
parent: only masters under this group (e.g. 'Sundry Debtors'), including sub-groups.
search: only names containing this text.
from_date / to_date: the period for opening and closing balances (blank = current financial year).
```

Arguments: `master_type`, `parent`?, `search`?, `from_date`?, `to_date`?, `limit`?, `offset`?  (`?` = optional)

### `tally_get_master`  (read)

Everything Tally stores about one master (all fields, exactly as Tally names them), plus a short readable summary. Use the exact name.

Arguments: `master_type`, `name`  (`?` = optional)

### `tally_list_vouchers`  (read)

The day book: vouchers for a period, oldest first.

```
Dates: YYYY-MM-DD, or leave blank for the current financial year, or from_date='FY2024-25'.
voucher_type: Sales, Purchase, Payment, Receipt, Contra, Journal, Credit Note, Debit Note,
Stock Journal, Payroll ... or any custom type name.
ledger: only vouchers touching this ledger. party: only this party.
search: text in narration / number / reference. min_amount: only vouchers of at least this value.
include_entries=true adds the ledger lines and item lines.
source: 'auto', 'collection' (fast) or 'daybook' (Tally's full Day Book export).
```

Arguments: `from_date`?, `to_date`?, `voucher_type`?, `ledger`?, `party`?, `search`?, `min_amount`?, `include_entries`?, `limit`?, `offset`?, `source`?  (`?` = optional)

### `tally_get_voucher`  (read)

One voucher in full: ledger lines, bill references, cost centres, bank details, item lines. Identify it by master_id (from tally_list_vouchers), or guid, or number + voucher_type (+ date). all_fields=true also returns every raw Tally field.

Arguments: `master_id`?, `guid`?, `number`?, `voucher_type`?, `date`?, `all_fields`?  (`?` = optional)

## Step 3 - Reports

### `tally_trial_balance`  (read)

Trial balance: opening and closing balance of every ledger (Dr / Cr columns) with totals.

```
level: 'ledger' (default), 'group' (immediate group) or 'primary' (top-level heads).
group: only ledgers under this group. Dates blank = current financial year.
```

Arguments: `from_date`?, `to_date`?, `level`?, `group`?, `include_zero`?  (`?` = optional)

### `tally_balance_sheet`  (read)

Balance sheet as on a date: liabilities and assets by head, with net profit for the year and closing stock. detailed=true lists the ledgers under each head.

```
'difference' should be 0. If it is not, the books have a difference in opening balances, or stock
is valued in a way this summary cannot see - compare with tally_native_report('Balance Sheet').
```

Arguments: `as_of`?, `from_date`?, `detailed`?  (`?` = optional)

### `tally_profit_loss`  (read)

Profit & loss for a period: trading account (sales, purchases, direct items, stock) down to gross profit, then indirect income and expenses down to net profit. detailed=true lists the ledgers under each head.

Arguments: `from_date`?, `to_date`?, `detailed`?  (`?` = optional)

### `tally_ledger_statement`  (read)

Ledger account statement: opening balance, every transaction with running balance, closing balance. Works for any ledger - party, bank, cash, expense, tax.

Arguments: `ledger`, `from_date`?, `to_date`?, `limit`?, `offset`?  (`?` = optional)

### `tally_outstandings`  (read)

Pending bills: money to receive from customers (kind='receivable') or to pay to suppliers (kind='payable'), bill by bill, with overdue days, ageing buckets and party-wise totals. party: only this party. ageing_days: bucket edges, default [30, 60, 90, 180].

Arguments: `kind`?, `party`?, `as_of`?, `ageing_days`?  (`?` = optional)

### `tally_cash_bank_balances`  (read)

Cash and bank position as on a date: every cash, bank and bank overdraft ledger with its balance, and the net total.

Arguments: `as_of`?  (`?` = optional)

### `tally_group_summary`  (read)

Drill into one group: its sub-groups and ledgers with opening and closing balances. Examples: 'Sundry Debtors', 'Indirect Expenses', 'Duties & Taxes', 'Current Assets'.

Arguments: `group`, `from_date`?, `to_date`?  (`?` = optional)

### `tally_monthly_summary`  (read)

Month-by-month debit and credit totals for one ledger or one whole group - e.g. monthly sales ('Sales Accounts'), monthly expenses ('Indirect Expenses') or a bank ledger.

Arguments: `ledger`?, `group`?, `from_date`?, `to_date`?  (`?` = optional)

### `tally_register`  (read)

Sales / purchase register: one row per invoice with party, GSTIN, taxable value, CGST, SGST, IGST, cess and total - plus totals for the period. kind: sales, purchase, credit_note or debit_note (notes are shown at their own value).

Arguments: `kind`?, `from_date`?, `to_date`?, `party`?, `limit`?, `offset`?  (`?` = optional)

### `tally_stock_summary`  (read)

Stock summary: every item with opening and closing quantity, rate and value, HSN and GST rate, plus the total stock value. Flags items with negative stock. stock_group: only items under this stock group. only_in_stock=true hides nil-stock items.

Arguments: `as_of`?, `from_date`?, `stock_group`?, `search`?, `only_in_stock`?, `limit`?, `offset`?  (`?` = optional)

### `tally_stock_movement`  (read)

Stock ledger of one item: every voucher that moved it in or out, with quantity, rate, value and the running quantity. Also shows total inward / outward and the average purchase and sale rate.

Arguments: `item`, `from_date`?, `to_date`?, `limit`?  (`?` = optional)

### `tally_native_report`  (read)

Export any of Tally's own reports exactly as Tally computes it, by its report name.

```
Common names: Trial Balance, Balance Sheet, Profit and Loss, Cash Flow, Funds Flow,
Ratio Analysis, Stock Summary, Bills Receivable, Bills Payable, Sales Register, Purchase Register,
Ledger Vouchers, GSTR-1, GSTR-3B, Pay Sheet (full list: tally_list_native_reports).
variables: report options as Tally variables, e.g. {"LEDGERNAME": "Cash"} for Ledger Vouchers,
{"GROUPNAME": "Sundry Debtors"} for Group Summary, {"STOCKITEMNAME": "Ball Valve"} for
Stock Vouchers. raw=true returns the XML text instead of rows.
```

Arguments: `report`, `from_date`?, `to_date`?, `variables`?, `raw`?, `limit`?  (`?` = optional)

### `tally_list_native_reports`  (read)

Names of Tally's built-in reports that tally_native_report can export. Any other report or custom TDL report name available in your Tally also works.

## Step 4 - GST, TDS, bank and audit

### `tally_gst_summary`  (read)

GST position for a period, from the books: output tax on sales, input tax on purchases, the net payable by head (CGST / SGST / IGST / cess), other movements such as tax payments and adjustments, and the closing balance of each GST ledger. Use it to review a month before filing GSTR-3B. Dates blank = current financial year; for a month pass both dates.

Arguments: `from_date`?, `to_date`?  (`?` = optional)

### `tally_gstr1`  (read)

GSTR-1 working data from the books: B2B invoices (registered buyers), B2C sales by state, credit notes to registered buyers, totals, and invoices that need attention (missing or invalid GSTIN, no place of supply). For Tally's own computation use tally_native_report('GSTR-1').

Arguments: `from_date`?, `to_date`?  (`?` = optional)

### `tally_hsn_summary`  (read)

HSN-wise summary of goods sold (or purchased): quantity, taxable value and GST rate per HSN code, taken from invoice item lines and each item's HSN. Lists items that have no HSN code. kind: sales or purchase.

Arguments: `kind`?, `from_date`?, `to_date`?  (`?` = optional)

### `tally_validate_gstin`  (read)

Check a GSTIN offline: length, format and check digit; returns the state and the PAN inside it. This catches typing errors. It does not confirm the registration is active on the GST portal.

Arguments: `gstin`  (`?` = optional)

### `tally_tds_summary`  (read)

TDS for a period, from the books: tax deducted and tax deposited for each TDS ledger (section), the balance still payable, and deductions party by party with PAN - the working data for the quarterly TDS return (26Q / 27Q). Flags deductees with no PAN.

Arguments: `from_date`?, `to_date`?  (`?` = optional)

### `tally_bank_reconciliation`  (read)

Bank reconciliation statement for one bank ledger: balance as per books, cheques issued but not yet presented, deposits not yet credited, and the resulting balance as per bank. An entry counts as cleared when it has a bank date on or before to_date. Start from_date early enough to include old unpresented cheques.

Arguments: `bank_ledger`, `from_date`?, `to_date`?  (`?` = optional)

### `tally_audit_checks`  (read)

Audit review of a period. Scans every voucher and ledger and reports: cash payments above the Sec 40A(3) limit, cash receipts at or above the Sec 269ST limit, days the cash balance went negative, entries dated on Sundays, vouchers without narration, duplicate voucher numbers, a supplier bill booked twice, large round-sum journals, debtors with credit balances and creditors with debit balances, suspense balances, and the largest vouchers. Each finding lists the vouchers so they can be opened with tally_get_voucher.

Arguments: `from_date`?, `to_date`?, `cash_payment_limit`?, `cash_receipt_limit`?  (`?` = optional)

### `tally_data_health`  (read)

Master-data quality check: party ledgers with missing or invalid GSTIN, GSTIN state not matching the ledger's state, the same GSTIN on several ledgers, parties without PAN or state, look-alike duplicate ledger names, and stock items without HSN code, GST rate or unit.

## Step 5 - Create and edit

### `tally_create_ledger`  (write)

Create a ledger (customer, supplier, bank, expense, income, tax ...).

```
group: the Tally group it sits under, e.g. 'Sundry Debtors', 'Sundry Creditors', 'Bank Accounts',
'Indirect Expenses', 'Sales Accounts', 'Duties & Taxes'.
opening_type: 'Dr' or 'Cr'. gst_registration_type: Regular, Composition, Unregistered, Consumer.
For tax ledgers: tax_type 'GST' / 'TDS' and gst_duty_head 'CGST', 'SGST/UTGST', 'IGST'
(Tally.ERP 9: 'Central Tax', 'State Tax', 'Integrated Tax').
fields: any other Tally tags, e.g. {"ISCOSTCENTRESON": "Yes"}.
```

Arguments: `name`, `group`, `opening_balance`?, `opening_type`?, `gstin`?, `gst_registration_type`?, `state`?, `country`?, `address`?, `pincode`?, `pan`?, `email`?, `mobile`?, `mailing_name`?, `bill_wise`?, `credit_period_days`?, `tax_type`?, `gst_duty_head`?, `alias`?, `fields`?  (`?` = optional)

### `tally_create_group`  (write)

Create an accounting group under an existing group (e.g. 'North Zone Debtors' under 'Sundry Debtors'). Use parent='Primary' only for a new top-level group, and then also pass fields such as {"ISREVENUE": "No", "ISDEEMEDPOSITIVE": "Yes"}.

Arguments: `name`, `parent`, `fields`?  (`?` = optional)

### `tally_create_stock_item`  (write)

Create a stock item. unit must already exist (see tally_list_masters unit; create one with tally_save_master). gst_rate is the total GST % (e.g. 18); taxability: Taxable, Exempt, Nil Rated. Opening stock: give opening_qty with opening_rate or opening_value.

Arguments: `name`, `unit`, `stock_group`?, `category`?, `hsn`?, `gst_rate`?, `taxability`?, `opening_qty`?, `opening_rate`?, `opening_value`?, `godown`?, `description`?, `alias`?, `fields`?  (`?` = optional)

### `tally_save_master`  (write)

Create or edit ANY master using Tally's own field names. This is the general-purpose writer.

```
action: 'create' or 'alter' (alter changes only the fields you pass).
fields: Tally tags and values. Nested lists use a '.LIST' key. Examples:
unit          {"ISSIMPLEUNIT": "Yes", "ORIGINALNAME": "Numbers", "DECIMALPLACES": 0}
godown        {"PARENT": ""}
cost_centre   {"PARENT": "", "CATEGORY": "Primary Cost Category"}
stock_group   {"PARENT": ""}
voucher_type  {"PARENT": "Sales", "NUMBERINGMETHOD": "Automatic"}
ledger alter  {"PARENT": "Sundry Creditors", "EMAIL": "a@b.com"}
employee      {"PARENT": "Staff", "DATEOFJOIN": "20240401", "DESIGNATION": "Clerk"}
Read an existing master with tally_get_master to see the exact tags it uses.
```

Arguments: `master_type`, `name`, `fields`?, `action`?  (`?` = optional)

### `tally_rename_master`  (write)

Rename a master. Existing vouchers follow the new name automatically.

Arguments: `master_type`, `name`, `new_name`  (`?` = optional)

### `tally_create_voucher`  (write)

Create an accounting voucher: Payment, Receipt, Contra, Journal, or a Sales / Purchase / Credit Note / Debit Note without stock items (service invoices).

```
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
Contra:  Dr one cash/bank ledger, Cr another.     Journal: any Dr / Cr adjustment.
```

Arguments: `voucher_type`, `date`, `entries`, `narration`?, `number`?, `party`?, `reference`?, `reference_date`?, `optional`?, `fields`?  (`?` = optional)

### `tally_create_invoice`  (write)

Create an item invoice with stock and GST.

```
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
tax and invoice total - check it with dry_run=true before posting.
```

Arguments: `kind`, `date`, `party`, `items`, `ledger`, `gst`?, `taxes`?, `charges`?, `round_off_ledger`?, `number`?, `reference`?, `reference_date`?, `narration`?, `place_of_supply`?, `credit_days`?, `bill_name`?, `bill_type`?, `godown`?, `voucher_type`?, `fields`?  (`?` = optional)

### `tally_create_stock_journal`  (write)

Stock journal: move, consume or produce stock without an accounting entry.

```
consumed: items going OUT (source). produced: items coming IN (destination).
Each line: {"item": "Steel Rod", "qty": 5, "unit": "Kg", "rate": 60, "godown": "Main Location"}.
Godown transfer = the same item in consumed (from godown) and produced (to godown).
```

Arguments: `date`, `consumed`?, `produced`?, `narration`?, `number`?, `voucher_type`?  (`?` = optional)

### `tally_alter_voucher`  (write)

Edit an existing voucher. Identify it like tally_get_voucher, then pass only what should change.

```
new_entries replaces ALL ledger lines of an accounting voucher (same format as
tally_create_voucher); item invoices keep their item lines, so change those by deleting and
re-creating the invoice. new_fields sets any other top-level Tally tag.
The voucher is re-read afterwards and returned so the change can be checked.
```

Arguments: `master_id`?, `guid`?, `number`?, `voucher_type`?, `date`?, `new_date`?, `new_number`?, `new_narration`?, `new_reference`?, `new_entries`?, `new_fields`?  (`?` = optional)

### `tally_bulk_import`  (write)

Post many records in one go, e.g. from a bank statement or an Excel sheet.

```
vouchers:    each item takes the same arguments as tally_create_voucher
(voucher_type, date, entries, narration, number, party, reference).
ledgers:     each item takes the same arguments as tally_create_ledger (name, group, gstin ...).
stock_items: each item takes the same arguments as tally_create_stock_item (name, unit ...).
Masters are posted first so vouchers can use them. Each record is posted on its own, so one bad
row does not block the rest; the result lists every failure with its row number.
```

Arguments: `vouchers`?, `ledgers`?, `stock_items`?, `stop_on_error`?  (`?` = optional)

### `tally_import_xml`  (write)

Import ready-made Tally XML objects (the content that goes inside <TALLYMESSAGE>): one or more <VOUCHER ...> or master elements such as <LEDGER ...>. For anything the other write tools do not cover - sales / purchase orders, delivery notes, payroll vouchers, price lists, budgets. kind: 'vouchers' or 'masters'. XML that deletes or cancels anything (ACTION="Delete" / "Cancel") needs access = 'full' and confirm=true, like the dedicated delete tools.

Arguments: `xml`, `kind`?, `confirm`?  (`?` = optional)

## Step 6 - Remove (needs access = full)

### `tally_delete_master`  (delete)

Delete a master. Tally refuses if it is used in any voucher or has children. Needs access = 'full' and confirm=true.

Arguments: `master_type`, `name`, `confirm`?  (`?` = optional)

### `tally_cancel_voucher`  (delete)

Cancel a voucher: the number stays in the books, marked cancelled, with no financial effect. Preferred over deleting for invoices already issued. Needs access = 'full' and confirm=true.

Arguments: `master_id`?, `guid`?, `number`?, `voucher_type`?, `date`?, `reason`?, `confirm`?  (`?` = optional)

### `tally_delete_voucher`  (delete)

Permanently delete a voucher. Cannot be undone. Needs access = 'full' and confirm=true.

Arguments: `master_id`?, `guid`?, `number`?, `voucher_type`?, `date`?, `confirm`?  (`?` = optional)

## Anything else

### `tally_sql`  (read)

Run an ODBC-style SELECT against Tally, e.g. SELECT $Name, $Parent, $ClosingBalance FROM Ledger WHERE $Parent = 'Sundry Debtors'

```
via: 'odbc' uses Tally's ODBC driver (Windows, driver installed); 'xml' runs the same query over
Tally's XML port (works everywhere); 'auto' tries ODBC and falls back to XML.
Read-only: only SELECT is accepted. Call tally_odbc_tables for table and field names.
```

Arguments: `sql`, `via`?, `limit`?, `from_date`?, `to_date`?  (`?` = optional)

### `tally_odbc_tables`  (read)

Table names, common field names and example queries for tally_sql, and whether the Tally ODBC driver is usable on this machine.

### `tally_tdl_collection`  (read)

Build and run a custom TDL collection - the most flexible way to read Tally.

```
object_type: any Tally object type, e.g. Ledger, Group, Voucher, StockItem, Bills, CostCentre,
Godown, VoucherType, Company. fields: method names to fetch, e.g. ["Name", "Parent",
"ClosingBalance"]; use "*" for every stored field and "AllLedgerEntries.*" for sub-lists.
filters: name -> TDL formula, all must be true, e.g.
{"BigDebtors": "$ClosingBalance < -100000"}   (debit balances are negative in Tally)
{"OnlySales": "$$IsSales:$VoucherTypeName"}
compute: extra calculated fields, name -> TDL formula. child_of: only objects under this parent.
raw=true returns the XML.
```

Arguments: `object_type`, `fields`, `filters`?, `child_of`?, `compute`?, `from_date`?, `to_date`?, `limit`?, `raw`?  (`?` = optional)

### `tally_evaluate`  (read)

Evaluate a TDL function inside Tally and return its result. Examples: function='$$LicenseInfo', params=['SerialNumber'] ; function='$$LicenseInfo', params=['IsEducationalMode'].

Arguments: `function`, `params`?  (`?` = optional)

### `tally_raw_xml`  (write)

Send a complete Tally XML request (<ENVELOPE>...</ENVELOPE>) exactly as written and return Tally's reply. The escape hatch for anything else Tally's XML interface supports. Export requests need no confirmation. Import requests change data, so they need confirm_write=true and write access; deletes and cancels need access = 'full'.

Arguments: `xml`, `confirm_write`?  (`?` = optional)

### `search_mentions`  (read)

Used by the app's @-mention picker: finds ledgers, groups and stock items whose name contains the query. Assistants should use tally_search instead.

Arguments: `query`  (`?` = optional)
