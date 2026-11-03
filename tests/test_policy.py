"""Administrator policy: a ceiling the assistant cannot change through MCP."""

from __future__ import annotations

import json

import pytest
from conftest import Caller, ToolFailed

from rajtally_mcp import config, policy

COMPANY = "Demo Traders Pvt Ltd"
RENT = [{"ledger": "Rent", "dr": 500}, {"ledger": "Cash", "cr": 500}]


def server(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    policy.reset()
    config.reset()
    return Caller()


def names(call):
    return {t.name for t in call.tools()}


def test_default_access_is_read_only(tally, monkeypatch):
    monkeypatch.delenv("TALLY_ACCESS")
    call = server(monkeypatch)
    assert call("tally_settings_read")["values"]["access"] == "read_only"
    with pytest.raises(ToolFailed, match="read_only"):
        call("tally_create_voucher", voucher_type="Payment", date="2026-05-10", entries=RENT)


def test_locked_read_profile_hides_writes_advanced_and_switching(tally, monkeypatch):
    call = server(monkeypatch, RAJTALLY_LOCK="1", TALLY_COMPANY=COMPANY, RAJTALLY_TOOL_PROFILE="read")
    tools = names(call)
    assert {"tally_trial_balance", "tally_outstandings", "tally_gst_summary", "tally_policy"} <= tools
    forbidden = {"tally_create_voucher", "tally_delete_voucher", "tally_raw_xml", "tally_sql", "tally_evaluate",
                 "tally_tdl_collection", "tally_import_xml", "tally_use_company", "tally_settings_update",
                 "tally_settings_read", "tally_alter_voucher"}
    assert not tools & forbidden
    pol = call("tally_policy")
    assert pol["locked"] and pol["company"] == COMPANY and pol["tool_profile"] == "read"
    assert call("tally_trial_balance")["difference"] == 0.0
    with pytest.raises(ToolFailed):  # not registered at all
        call("tally_raw_xml", xml="<ENVELOPE/>")


def test_locked_company_cannot_be_redirected(tally, monkeypatch):
    call = server(monkeypatch, RAJTALLY_LOCK="1", TALLY_COMPANY=COMPANY, RAJTALLY_TOOL_PROFILE="read")
    with pytest.raises(ToolFailed, match="locked to company"):
        call("tally_trial_balance", company="Some Other Co")
    # a blank name does not fall back to "whatever is active in Tally": it uses the pinned company
    assert call("tally_trial_balance", company="")["difference"] == 0.0
    assert call("tally_trial_balance", company=COMPANY.upper())["difference"] == 0.0


def test_locked_server_ignores_saved_settings_file(tally, monkeypatch, tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"host": "evil.example", "port": 1, "access": "full",
                                                        "company": "Other"}))
    call = server(monkeypatch, RAJTALLY_LOCK="1", TALLY_COMPANY=COMPANY, RAJTALLY_ACCESS_CEILING="read_write")
    cfg = config.get()
    assert cfg.host == "127.0.0.1" and cfg.company == COMPANY and cfg.access == "read_write"
    assert call("tally_trial_balance")["difference"] == 0.0


def test_access_ceiling_clamps_and_refuses(tally, monkeypatch):
    call = server(monkeypatch, TALLY_ACCESS="full", RAJTALLY_ACCESS_CEILING="read_write")
    assert call("tally_settings_read")["values"]["access"] == "read_write"
    with pytest.raises(ToolFailed, match="ceiling"):
        call("tally_settings_update", set={"access": "full"})
    target = call("tally_list_vouchers", voucher_type="Payment")["vouchers"][0]
    with pytest.raises(ToolFailed, match="need access = 'full'"):
        call("tally_delete_voucher", master_id=target["master_id"], confirm=True)


def test_unlocked_settings_cannot_exceed_ceiling_via_use_company_either(tally, monkeypatch):
    call = server(monkeypatch, RAJTALLY_ACCESS_CEILING="read_only")
    assert call("tally_settings_read")["values"]["access"] == "read_only"


def test_post_profile_creates_vouchers_only(tally, monkeypatch):
    call = server(monkeypatch, RAJTALLY_LOCK="1", TALLY_COMPANY=COMPANY, RAJTALLY_TOOL_PROFILE="post",
                  RAJTALLY_ACCESS_CEILING="read_write", RAJTALLY_COMPANY_GUID="demo-company")
    tools = names(call)
    assert "tally_create_voucher" in tools and "tally_list_vouchers" in tools
    assert not tools & {"tally_alter_voucher", "tally_create_ledger", "tally_delete_voucher", "tally_bulk_import"}
    done = call("tally_create_voucher", voucher_type="Payment", date="2026-05-10", entries=RENT, reference="RAJ-1")
    assert done["ok"] and done["created"] == 1


def test_write_refused_when_pinned_guid_does_not_match(tally, monkeypatch):
    call = server(monkeypatch, RAJTALLY_LOCK="1", TALLY_COMPANY=COMPANY, RAJTALLY_TOOL_PROFILE="post",
                  RAJTALLY_ACCESS_CEILING="read_write", RAJTALLY_COMPANY_GUID="someone-elses-guid")
    with pytest.raises(ToolFailed, match="GUID does not match"):
        call("tally_create_voucher", voucher_type="Payment", date="2026-05-10", entries=RENT)


def test_allow_list_narrows_profile(tally, monkeypatch):
    call = server(monkeypatch, RAJTALLY_LOCK="1", TALLY_COMPANY=COMPANY, RAJTALLY_TOOL_PROFILE="read",
                  RAJTALLY_ALLOW_TOOLS="tally_trial_balance,tally_policy")
    assert names(call) - {"search_mentions"} == {"tally_trial_balance", "tally_policy"}


def test_bad_policy_values_fail_fast(monkeypatch):
    monkeypatch.setenv("RAJTALLY_TOOL_PROFILE", "everything")
    policy.reset()
    with pytest.raises(policy.PolicyError):
        policy.get()
    monkeypatch.setenv("RAJTALLY_TOOL_PROFILE", "read")
    monkeypatch.setenv("RAJTALLY_LOCK", "1")
    monkeypatch.delenv("TALLY_COMPANY", raising=False)
    policy.reset()
    with pytest.raises(policy.PolicyError, match="needs a company"):
        policy.get()
    policy.reset()
