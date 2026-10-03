---
name: tally
description: Work with Tally.ERP 9 / TallyPrime books - look up ledgers, vouchers and stock, produce financial, GST and TDS reports, run audit checks, and post or edit entries. Use for any accounting question about the user's Tally data.
---

# Working with Tally

Call `tally_guide` once for the map of every tool. The flow is always the same:

1. **Connect** - `tally_status`. If it reports a problem, fix that first (`tally_use_company`,
   `tally_settings_update`).
2. **Find exact names** - `tally_search` before any tool that takes a ledger, group or item name.
   Tally matches names exactly.
3. **Read or report** - pick the most specific tool:
   - balances and statements: `tally_trial_balance`, `tally_balance_sheet`, `tally_profit_loss`,
     `tally_ledger_statement`, `tally_group_summary`, `tally_cash_bank_balances`, `tally_monthly_summary`
   - parties: `tally_outstandings` (receivable / payable with ageing)
   - transactions: `tally_list_vouchers`, `tally_get_voucher`, `tally_register`
   - stock: `tally_stock_summary`, `tally_stock_movement`
   - compliance: `tally_gst_summary`, `tally_gstr1`, `tally_hsn_summary`, `tally_tds_summary`,
     `tally_bank_reconciliation`, `tally_audit_checks`, `tally_data_health`
   - Tally's own figures for any report: `tally_native_report`
4. **Write** - always in this order:
   1. call the write tool with `dry_run: true`;
   2. show the user the summary (party, date, amounts, tax, total) and ask them to confirm;
   3. call it again without `dry_run`;
   4. check `ok` in the result. If `ok` is false, read `line_errors` - Tally says exactly what is
      missing (usually a ledger or item name that does not exist) - fix it and retry.

## Rules

- Dates are `YYYY-MM-DD`. Blank dates mean the current Indian financial year (April-March);
  `from_date: "FY2024-25"` selects a whole year.
- Amounts are positive numbers with `dr` or `cr`. Debits must equal credits.
- Payment = Dr expense or party, Cr cash/bank. Receipt = Dr cash/bank, Cr party or income.
  Contra = between cash and bank ledgers. Journal = everything else.
- Item invoices go through `tally_create_invoice`; service invoices without stock go through
  `tally_create_voucher` with the Sales or Purchase voucher type.
- Never delete to correct a mistake when an edit will do: use `tally_alter_voucher`. Prefer
  `tally_cancel_voucher` over `tally_delete_voucher` for invoices already issued.
- Delete and cancel need `access: full` and `confirm: true`. Only raise the access level when the
  user asks for it.
- The GST and TDS tools produce working papers from the books. Say so; they are not filed returns.
- For anything without a dedicated tool (orders, delivery notes, payroll vouchers, price lists):
  read a similar existing object with `tally_get_voucher` / `tally_get_master` using
  `all_fields: true`, then post with `tally_import_xml`.
- If a tool says Tally cannot be reached, ask the user to open Tally and the company; do not retry
  in a loop.
