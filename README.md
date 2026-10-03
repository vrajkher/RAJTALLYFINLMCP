# RAJ Tally FINL MCP

An MCP server that lets an AI assistant work with **Tally.ERP 9** and **TallyPrime**: read the books,
produce reports, and post or edit entries — in plain language.

It is built on the official MCP Python SDK with the
[OpenAI MCP Extensions](https://github.com/openai/mcp-extensions) (structured settings and composer
@-mentions), and works in ChatGPT / Codex, Claude Desktop, Claude Code and any other MCP client.

```
You:  "Who owes us money for more than 90 days?"
You:  "Book today's rent of 15,000 paid from HDFC by cheque 000123."
You:  "Show the GST position for September and list invoices with a wrong GSTIN."
```

---

## What it covers

| Area | What you can do |
| --- | --- |
| **Connect** | Check Tally is running, list open companies, pick a company, read company profile |
| **Masters** | Ledgers, groups, stock items, stock groups and categories, units, godowns, cost centres and categories, voucher types, currencies, budgets, employees, pay heads, attendance types — list, search, read every field, create, edit, rename, delete |
| **Vouchers** | Day book with filters, full voucher detail; create Payment / Receipt / Contra / Journal, item invoices with GST (sales, purchase, credit note, debit note), stock journals; edit, cancel, delete; bulk import |
| **Financial reports** | Trial balance, balance sheet, profit & loss, ledger statement with running balance, receivables and payables with ageing, cash and bank position, group drill-down, month-wise summary |
| **Inventory** | Stock summary with value, HSN and GST rate; item-wise stock movement |
| **GST** | Sales / purchase registers, GST summary (output, input credit, net payable), GSTR-1 working data (B2B / B2C / credit notes), HSN summary, GSTIN check-digit validation |
| **TDS and bank** | TDS deducted / deposited / payable with deductee list, bank reconciliation statement |
| **Audit** | Sec 40A(3) cash payments, Sec 269ST cash receipts, negative cash days, duplicate numbers, double-booked supplier bills, round-sum journals, wrong-side balances, master-data health |
| **Anything else** | ODBC-style `SELECT`, any Tally report by name, custom TDL collections, TDL functions, raw XML |

53 tools in all. The full list with arguments is in [docs/TOOLS.md](docs/TOOLS.md).

---

## Set it up in 4 steps

### Step 1 — Turn on Tally's server

**TallyPrime:** `F1 Help` → `Settings` → `Connectivity` → `Client/Server configuration`

- TallyPrime acts as: **Both**
- Enable ODBC: **Yes**
- Port: **9000**

Restart TallyPrime and open your company.
*(Tally.ERP 9: `F12 Configure` → `Advanced Configuration`, same three settings.)*

Check it: open <http://localhost:9000> in a browser. You should see *"TallyPrime Server is Running"*.

### Step 2 — Install

You need Python 3.10 or newer on the same computer as Tally (or one that can reach it on the network).

```bash
pip install git+https://github.com/vrajkher/RAJTALLYFINLMCP
```

To also use Tally's ODBC driver (Windows): `pip install pyodbc`.

### Step 3 — Add it to your assistant

**Claude Desktop** — `Settings` → `Developer` → `Edit Config`, then add:

```json
{
  "mcpServers": {
    "rajtally": {
      "command": "rajtally-mcp"
    }
  }
}
```

If Windows cannot find `rajtally-mcp`, use `"command": "python", "args": ["-m", "rajtally_mcp"]` instead.

**Claude Code**

```bash
claude mcp add rajtally -- rajtally-mcp
```

**ChatGPT / Codex (as a plugin)** — this repository is a plugin marketplace:

```bash
codex plugin marketplace add vrajkher/RAJTALLYFINLMCP
```

Then install **RAJ Tally FINL** from the plugin list. The plugin starts the server with
[`uvx`](https://docs.astral.sh/uv/), so only `uv` needs to be installed. Host, port, company and
access level are exposed as structured plugin settings, and typing `@` in the composer searches your
ledgers and stock items (where the app supports those extensions).

**No install at all** (any client, needs `uv`):

```json
{ "command": "uvx", "args": ["--from", "git+https://github.com/vrajkher/RAJTALLYFINLMCP", "rajtally-mcp"] }
```

### Step 4 — Check the connection

```bash
rajtally-mcp --check
```

This prints what Tally answered: product, open companies, the active company. Then just ask your
assistant: *"Check the Tally connection and show me the trial balance."*

---

## How to use it

The tools follow one flow. `tally_guide` returns this map to the assistant.

| Step | Purpose | Tools |
| --- | --- | --- |
| 1 | Connect | `tally_status` → `tally_list_companies` → `tally_use_company` |
| 2 | Look up | `tally_search`, `tally_list_masters`, `tally_get_master`, `tally_list_vouchers`, `tally_get_voucher` |
| 3 | Report | `tally_trial_balance`, `tally_balance_sheet`, `tally_profit_loss`, `tally_ledger_statement`, `tally_outstandings`, `tally_cash_bank_balances`, `tally_group_summary`, `tally_monthly_summary`, `tally_register`, `tally_stock_summary`, `tally_stock_movement`, `tally_native_report` |
| 4 | Comply | `tally_gst_summary`, `tally_gstr1`, `tally_hsn_summary`, `tally_validate_gstin`, `tally_tds_summary`, `tally_bank_reconciliation`, `tally_audit_checks`, `tally_data_health` |
| 5 | Write | `tally_create_ledger`, `tally_create_group`, `tally_create_stock_item`, `tally_save_master`, `tally_create_voucher`, `tally_create_invoice`, `tally_create_stock_journal`, `tally_alter_voucher`, `tally_bulk_import`, `tally_import_xml` |
| 6 | Remove | `tally_rename_master`, `tally_delete_master`, `tally_cancel_voucher`, `tally_delete_voucher` |
| — | Anything else | `tally_sql`, `tally_tdl_collection`, `tally_evaluate`, `tally_raw_xml` |

Four rules cover almost everything:

1. **Dates** are `YYYY-MM-DD`. Leave them blank for the current financial year (April–March), or pass
   `FY2024-25`.
2. **Amounts** are positive numbers with `dr` or `cr`. Reports come back with Dr / Cr columns.
3. **Names** must match Tally exactly — `tally_search` finds the exact name from a part of it.
4. **Every write has `dry_run`.** With `dry_run: true` the tool shows what it would post and changes
   nothing.

### Example: a payment

```json
{
  "voucher_type": "Payment",
  "date": "2026-04-05",
  "narration": "Shop rent for April",
  "entries": [
    { "ledger": "Rent", "dr": 15000 },
    { "ledger": "HDFC Bank", "cr": 15000,
      "bank": { "transaction_type": "Cheque", "instrument_number": "000123" } }
  ]
}
```

### Example: a GST sales invoice

```json
{
  "kind": "sales",
  "date": "2026-04-06",
  "party": "Shree Ganesh Hardware",
  "ledger": "Sales @ 18%",
  "items": [ { "item": "Ball Valve 2 inch", "qty": 10, "unit": "Nos", "rate": 450, "gst_rate": 18 } ],
  "gst": { "cgst_ledger": "CGST", "sgst_ledger": "SGST", "igst_ledger": "IGST", "interstate": false },
  "round_off_ledger": "Round Off"
}
```

The result shows taxable value 4,500.00, tax 810.00 and invoice total 5,310.00 before anything is posted.

---

## ODBC and XML — which does what

Tally offers two doors, and this server uses both:

| | ODBC | XML over HTTP (port 9000) |
| --- | --- | --- |
| Read | Yes — `SELECT $Name, $ClosingBalance FROM Ledger` | Yes — everything |
| Create / edit / delete | **No. Tally's ODBC is read-only.** | Yes |
| Needs | Windows + Tally ODBC driver + `pyodbc` | Nothing extra |

So `tally_sql` runs your `SELECT` through ODBC when the driver is present, and otherwise runs the
same query over XML — you get the same rows either way. All writing goes through XML.

---

## Safety

Your books are protected by three layers:

| Access level | Read | Create and edit | Delete and cancel |
| --- | --- | --- | --- |
| `read_only` | Yes | No | No |
| `read_write` (default) | Yes | Yes | No |
| `full` | Yes | Yes | Yes, with `confirm: true` |

- Change the level with `tally_settings_update`, or start with `rajtally-mcp --access read_only`.
- Every write tool accepts `dry_run: true`.
- Vouchers are checked before sending: debits must equal credits, and a GSTIN must pass its check
  digit. Tally's own error (for example *"Ledger 'X' does not exist!"*) is returned word for word.

**Take a Tally backup before the first write session, and try writes on a test company first.**

---

## Settings

| Setting | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `host` | `TALLY_HOST` | `localhost` | Computer running Tally |
| `port` | `TALLY_PORT` | `9000` | Tally's HTTP port |
| `company` | `TALLY_COMPANY` | *(blank)* | Exact company name; blank = the company active in Tally |
| `access` | `TALLY_ACCESS` | `read_write` | `read_only`, `read_write` or `full` |
| `odbcDsn` | `TALLY_ODBC_DSN` | *(auto)* | ODBC data source, e.g. `TallyODBC64_9000` |
| `timeout` | `TALLY_TIMEOUT` | `60` | Seconds to wait for Tally |
| `gstSchema` | `TALLY_GST_SCHEMA` | `prime` | `prime` = TallyPrime 3 or later; `erp9` = Tally.ERP 9 / older TallyPrime |
| — | `TALLY_ENCODING` | `utf-16` | `utf-16` keeps Gujarati / Hindi names intact |

Settings changed through the assistant are saved in `~/.rajtally/settings.json`.

---

## Try it without Tally

A small pretend Tally is included, with a sample company, ledgers, stock and nine vouchers:

```bash
rajtally-mcp --demo          # terminal 1: pretend Tally on port 9000
rajtally-mcp --check         # terminal 2: should show "Demo Traders Pvt Ltd"
```

Point your assistant at it and try every tool, including the write tools, with no risk.

---

## What has and has not been tested

Being straight about this matters for accounting software.

**Tested:** 60 automated tests run every tool through the real MCP protocol (in-process and over
stdio) against the built-in pretend Tally: reads, reports that tie out (trial balance and balance
sheet differences are zero), every write path, access levels, dry runs, Gujarati text, and both
OpenAI extensions. Run them with `pip install -e ".[dev]" && pytest`.

**Not yet tested: a live Tally installation.** This was built without access to one. The XML requests
follow Tally's documented formats, but Tally releases differ in details, so on your first run:

1. Run `rajtally-mcp --check`, then the read tools, and compare `tally_trial_balance` with Tally's
   own trial balance.
2. Try each write tool with `dry_run: true`, then for real **on a test company**.
3. If something does not match your Tally release, `tally_get_master` / `tally_get_voucher` with
   `all_fields: true` show exactly how your Tally stores it, and `tally_import_xml` /
   `tally_raw_xml` let you post in that exact shape. Please open an issue with the details.

The places most likely to vary by release: GST details on ledgers and stock items (hence the
`gstSchema` setting), and altering / cancelling vouchers.

**Known limits**

- ODBC cannot write (a Tally limitation, not this server's).
- Sales / purchase orders, delivery and receipt notes, payroll vouchers, price lists and budgets have
  no dedicated create tool; post them with `tally_import_xml`. They can all be read.
- Creating, opening, backing up or restoring a company, and e-invoice / e-way bill portal uploads,
  happen inside Tally itself and are not available through its XML interface.
- The balance sheet, P&L, GST and TDS tools are computed from ledger balances and vouchers so each
  figure can be traced. They are working papers, not filed returns. For Tally's own computation use
  `tally_native_report` (for example `Balance Sheet`, `GSTR-1`, `GSTR-3B`).
- Report tools read the whole period's vouchers. On very large companies, ask for a month at a time.

---

## Troubleshooting

| Message | Fix |
| --- | --- |
| *Cannot reach Tally at http://localhost:9000* | Open Tally and a company. Check Step 1. Check the port with `tally_settings_read`. |
| *Tally is running but no company is open* | Open the company in Tally. |
| *The selected company '…' is not open in Tally* | Open it in Tally, or call `tally_use_company`. |
| *Ledger 'X' does not exist!* | The name must match exactly. Use `tally_search`, or create it first. |
| *Voucher does not balance* | Total of `dr` lines must equal total of `cr` lines. |
| *This server is in read_only mode* | `tally_settings_update` with `{"set": {"access": "read_write"}}`. |
| *Deleting and cancelling need access = 'full'* | Set access to `full`, then call again with `confirm: true`. |
| GST details missing on a created ledger or item | Set `gstSchema` to match your Tally release. |
| Names in Gujarati / Hindi look wrong | Keep `TALLY_ENCODING=utf-16` (the default). |

---

## Project layout

```
src/rajtally_mcp/
  server.py        MCP server + OpenAI extensions (settings, @-mentions)
  tally_xml.py     the only file that talks to Tally: requests, parsing, dates, amounts
  builders.py      builds master / voucher / invoice XML
  core.py          access checks, master types, voucher parsing
  tools/           one file per step: connection, masters, vouchers, reports,
                   inventory, gst, compliance, advanced
  demo_tally.py    the pretend Tally
plugins/rajtally/  ChatGPT / Codex plugin: manifest, skills, icons
docs/TOOLS.md      every tool and its arguments
tests/             60 tests
```

## License

MIT
