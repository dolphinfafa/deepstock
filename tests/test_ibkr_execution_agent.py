from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from scripts.configure_deepstock_node_token import update_env as update_node_env
from scripts.ibkr_execution_agent import (
    AgentConfig,
    LIVE_CONFIRMATION,
    update_env,
    validate_plan,
)


def config(tmp_path: Path, *, mode: str = "paper") -> AgentConfig:
    account = "DU123456"
    salt = "test-only-long-salt"
    account_hash = hashlib.sha256(f"{salt}:{account}".encode()).hexdigest()
    return AgentConfig(
        mode=mode,
        host="127.0.0.1",
        port=7497 if mode == "paper" else 7496,
        client_id=77,
        account=account,
        account_hash_salt=salt,
        expected_account_hash=account_hash,
        read_only=True,
        live_execution_enabled=False,
        live_confirmation="",
        api_base_url="https://example.invalid/deepstock/api",
        node_token="token",
        local_notional_cap_usd=1000,
        max_data_age_days=3,
        state_path=tmp_path / "state.json",
    )


def plan(mode: str = "paper") -> dict:
    return {
        "id": "plan",
        "mode": mode,
        "status": "ready",
        "data_date": date.today().isoformat(),
        "total_notional_usd": 100,
        "orders": [
            {
                "order_id": "a" * 32,
                "symbol": "SGOV",
                "action": "BUY",
                "quantity": 1,
                "order_type": "LMT",
                "limit_price": 100,
                "currency": "USD",
                "exchange": "SMART",
            }
        ],
    }


def control(mode: str = "paper") -> dict:
    return {
        "mode": mode,
        "global_kill_switch": True,
        "live_trading_enabled": False,
        "wechat_test_passed": False,
        "live_notional_cap_usd": 1000,
        "latest_account_hash": None,
    }


def test_paper_plan_passes_with_account_allowlist(tmp_path: Path) -> None:
    value = config(tmp_path)
    validate_plan(plan(), value, control(), value.account_hash(), {})


def test_short_sale_and_over_cap_are_rejected(tmp_path: Path) -> None:
    value = config(tmp_path)
    short = plan()
    short["orders"][0]["action"] = "SELL"
    with pytest.raises(ValueError, match="short"):
        validate_plan(short, value, control(), value.account_hash(), {})
    large = plan()
    large["total_notional_usd"] = 1001
    with pytest.raises(ValueError, match="cap"):
        validate_plan(large, value, control(), value.account_hash(), {})


def test_live_requires_all_local_and_server_gates(tmp_path: Path) -> None:
    value = config(tmp_path, mode="live")
    live_plan = plan("live")
    live_control = {**control("live"), "global_kill_switch": False}
    with pytest.raises(ValueError, match="disabled"):
        validate_plan(live_plan, value, live_control, value.account_hash(), {})

    enabled = replace(
        value,
        live_execution_enabled=True,
        live_confirmation=LIVE_CONFIRMATION,
    )
    live_control.update(live_trading_enabled=True, wechat_test_passed=True)
    validate_plan(live_plan, enabled, live_control, enabled.account_hash(), {})


def test_wrong_account_hash_is_rejected(tmp_path: Path) -> None:
    value = config(tmp_path)
    with pytest.raises(ValueError, match="allowlist"):
        validate_plan(plan(), value, control(), "0" * 64, {})


def test_local_env_updates_preserve_existing_secrets(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("MASSIVE_API_KEY=preserve-me\nIBKR_MODE=paper\n", encoding="utf-8")
    update_env(path, {"IBKR_ACCOUNT": "DU123", "IBKR_MODE": "paper"})
    update_node_env(
        path,
        {
            "DEEPSTOCK_NODE_TOKEN": "x" * 48,
            "DEEPSTOCK_API_BASE_URL": "https://example.invalid/api",
        },
    )
    text = path.read_text(encoding="utf-8")
    assert "MASSIVE_API_KEY=preserve-me" in text
    assert text.count("IBKR_MODE=") == 1
    assert "IBKR_ACCOUNT=DU123" in text
    assert "DEEPSTOCK_NODE_TOKEN=" + "x" * 48 in text
