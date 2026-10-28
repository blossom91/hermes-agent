"""``ignore_at_all`` — @所有人/@everyone must not count as a self-mention when enabled.

Covers the three layers of the switch: ``_load_settings`` parsing (env + extra),
``_mentions_self`` semantics, the ``_admit`` gate, and the ``_apply_yaml_config``
bridge that carries top-level ``platforms.feishu.ignore_at_all`` into the adapter.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from tests.gateway.feishu_helpers import make_adapter_skeleton, make_message, make_sender


def bot_mention(open_id: str = "ou_me", name: str = "Hermes"):
    return SimpleNamespace(
        key="@_user_1",
        id=SimpleNamespace(open_id=open_id, user_id="", union_id=""),
        name=name,
        tenant_key="t",
    )


def at_all_message(text: str = "@_all hi", mentions: list | None = None):
    base = make_message(chat_type="group", chat_id="oc_group", mentions=mentions)
    base.content = json.dumps({"text": text})
    return base


# --- FeishuAdapterSettings wiring ------------------------------------------


@pytest.mark.parametrize(
    "env_value, extra, expected",
    [
        (None, {}, False),                       # default off — @all still wakes the bot
        ("true", {}, True),
        ("1", {}, True),
        ("yes", {}, True),
        ("on", {}, True),
        ("false", {}, False),
        (None, {"ignore_at_all": True}, True),   # config.yaml extra mapping
        (None, {"ignore_at_all": "true"}, True),
        (None, {"ignore_at_all": False}, False), # explicit false is a real value
        (None, {"ignore_at_all": "yes"}, True),
        ("true", {"ignore_at_all": False}, True),  # env wins over YAML
        ("false", {"ignore_at_all": True}, False),
    ],
)
def test_feishu_load_settings_ignore_at_all(monkeypatch, env_value, extra, expected):
    from plugins.platforms.feishu.adapter import FeishuAdapter

    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_test")
    monkeypatch.delenv("FEISHU_IGNORE_AT_ALL", raising=False)
    if env_value is not None:
        monkeypatch.setenv("FEISHU_IGNORE_AT_ALL", env_value)

    settings = FeishuAdapter._load_settings(extra=extra)
    assert settings.ignore_at_all is expected


# --- _mentions_self ---------------------------------------------------------


def test_at_all_counts_as_mention_by_default():
    adapter = make_adapter_skeleton(require_mention=True)
    assert adapter._mentions_self(at_all_message()) is True


def test_at_all_ignored_when_switch_on():
    adapter = make_adapter_skeleton(require_mention=True)
    adapter._ignore_at_all = True
    assert adapter._mentions_self(at_all_message()) is False


def test_at_all_placeholder_without_sdk_mentions_still_detected():
    # No `mentions` array on the event; the @_all placeholder in raw text is normalized
    # into an is_all mention ref — the second disjunct of the gate.
    adapter = make_adapter_skeleton(require_mention=True)
    adapter._ignore_at_all = True
    message = make_message(chat_type="group", chat_id="oc_group", mentions=None)
    message.content = json.dumps({"text": "@_all morning sync"})
    assert adapter._mentions_self(message) is False
    adapter._ignore_at_all = False
    assert adapter._mentions_self(message) is True


def test_explicit_bot_mention_beats_ignore_at_all():
    adapter = make_adapter_skeleton(require_mention=True)
    adapter._ignore_at_all = True
    message = at_all_message(text="@_all @Hermes ping", mentions=[bot_mention()])
    assert adapter._mentions_self(message) is True


def test_plain_group_text_never_mentions_self():
    adapter = make_adapter_skeleton(require_mention=True)
    adapter._ignore_at_all = True
    message = make_message(chat_type="group", chat_id="oc_group")
    message.content = json.dumps({"text": "just talking"})
    assert adapter._mentions_self(message) is False


# --- _admit integration -----------------------------------------------------


def _group_admit(adapter, message):
    return adapter._admit(make_sender(), message)


def test_admit_rejects_at_all_only_group_message_when_ignored():
    adapter = make_adapter_skeleton(require_mention=True, group_policy="open")
    adapter._ignore_at_all = True
    assert _group_admit(adapter, at_all_message()) == "group_policy_rejected"


def test_admit_accepts_at_all_group_message_by_default():
    adapter = make_adapter_skeleton(require_mention=True, group_policy="open")
    assert _group_admit(adapter, at_all_message()) is None


def test_admit_accepts_explicit_mention_even_with_ignore_at_all():
    adapter = make_adapter_skeleton(require_mention=True, group_policy="open")
    adapter._ignore_at_all = True
    assert _group_admit(adapter, at_all_message(text="@Hermes ping", mentions=[bot_mention()])) is None


# --- config.yaml bridge (_apply_yaml_config) --------------------------------


def test_yaml_bridge_seeds_ignore_at_all_and_env(monkeypatch):
    from plugins.platforms.feishu.adapter import _apply_yaml_config

    monkeypatch.delenv("FEISHU_IGNORE_AT_ALL", raising=False)
    saved = os.environ.get("FEISHU_IGNORE_AT_ALL")
    try:
        extra = _apply_yaml_config({}, {"ignore_at_all": True})
        assert extra == {"ignore_at_all": True}
        assert os.environ["FEISHU_IGNORE_AT_ALL"] == "true"
    finally:
        if saved is None:
            os.environ.pop("FEISHU_IGNORE_AT_ALL", None)
        else:
            os.environ["FEISHU_IGNORE_AT_ALL"] = saved


def test_yaml_bridge_does_not_clobber_explicit_env(monkeypatch):
    from plugins.platforms.feishu.adapter import _apply_yaml_config

    monkeypatch.setenv("FEISHU_IGNORE_AT_ALL", "false")
    extra = _apply_yaml_config({}, {"ignore_at_all": True})
    assert extra == {"ignore_at_all": True}   # extra still seeded (per-profile read)
    assert os.environ["FEISHU_IGNORE_AT_ALL"] == "false"  # explicit env wins


def test_yaml_bridge_false_is_a_real_value():
    from plugins.platforms.feishu.adapter import _apply_yaml_config

    saved = os.environ.get("FEISHU_IGNORE_AT_ALL")
    try:
        os.environ.pop("FEISHU_IGNORE_AT_ALL", None)
        extra = _apply_yaml_config({}, {"ignore_at_all": False})
        assert extra == {"ignore_at_all": False}
        assert os.environ["FEISHU_IGNORE_AT_ALL"] == "false"
    finally:
        if saved is None:
            os.environ.pop("FEISHU_IGNORE_AT_ALL", None)
        else:
            os.environ["FEISHU_IGNORE_AT_ALL"] = saved


def test_yaml_bridge_still_bridges_allow_bots_alone(monkeypatch):
    from plugins.platforms.feishu.adapter import _apply_yaml_config

    monkeypatch.delenv("FEISHU_ALLOW_BOTS", raising=False)
    saved = os.environ.get("FEISHU_ALLOW_BOTS")
    try:
        extra = _apply_yaml_config({}, {"allow_bots": "mentions"})
        assert extra == {"allow_bots": "mentions"}
    finally:
        if saved is None:
            os.environ.pop("FEISHU_ALLOW_BOTS", None)
        else:
            os.environ["FEISHU_ALLOW_BOTS"] = saved


def test_yaml_bridge_none_when_no_keys(monkeypatch):
    from plugins.platforms.feishu.adapter import _apply_yaml_config

    assert _apply_yaml_config({}, {"enabled": True}) is None
