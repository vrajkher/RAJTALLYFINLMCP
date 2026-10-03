---
name: onboarding
description: Connect RAJ Tally FINL to the user's Tally and choose a company and access level, when the user chooses Set up or asks for onboarding.
---

# Set up RAJ Tally FINL

Get the user connected to Tally in four short steps. Ask one question at a time and skip any the
user has already answered.

1. Call `tally_status` with `{}`.
   - If `connected` is false, tell the user to open Tally, open a company, and enable the HTTP server:
     **F1 Help > Settings > Connectivity > Client/Server configuration** - "TallyPrime acts as" = Both,
     "Enable ODBC" = Yes, Port = 9000 - then restart Tally. (Tally.ERP 9: F12 Configure > Advanced
     Configuration.) If Tally uses a different port or runs on another computer, call
     `tally_settings_update` with `{"set": {"port": 9001}}` or `{"set": {"host": "192.168.1.10"}}`,
     then call `tally_status` again.
2. Company. If more than one company is open, ask "Which company should I work on?" and call
   `tally_use_company` with the exact name. With one company open, use it without asking.
3. Access. Ask "Should I only read, or may I also create and edit entries?"
   Map the answer to `access`: only read -> `read_only`; create and edit -> `read_write`;
   also delete and cancel -> `full`. Call `tally_settings_update` with `{"set": {"access": "..."}}`
   only if it differs from the current value in `tally_settings_read`.
4. Version. If the user says they use Tally.ERP 9 (or TallyPrime older than release 3), call
   `tally_settings_update` with `{"set": {"gstSchema": "erp9"}}`. Otherwise leave it.

Confirm the saved values from the tool results; do not claim something was saved if the tool failed.
Finish by calling `tally_company_info` and offering two or three things to try, for example the trial
balance, receivables ageing, or the GST summary for last month.
