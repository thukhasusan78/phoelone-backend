from __future__ import annotations

from app.tools.otto_gate import (
    is_otto_motion_request,
    is_otto_stop_request,
    looks_like_noise_utterance,
    parse_otto_moving,
    should_dispatch_otto_tool,
)


def test_stop_intent() -> None:
    assert is_otto_stop_request("ရပ်")
    assert is_otto_stop_request("please stop")
    assert is_otto_stop_request("ပိတ်လိုက်")
    assert not is_otto_stop_request("")
    assert not is_otto_stop_request("Oh.")
    assert not is_otto_stop_request("မြန်မာ သီချင်းဖွင့်ပြ")


def test_motion_intent() -> None:
    assert is_otto_motion_request("ရှေ့ကို လမ်းလျှောက်")
    assert is_otto_motion_request("ရှေ့သွား")
    assert is_otto_motion_request("walk forward")
    assert is_otto_motion_request("ကခုန်ပေး")
    assert is_otto_motion_request("ရပ်")
    assert not is_otto_motion_request("")


def test_noise_utterance() -> None:
    assert looks_like_noise_utterance("")
    assert looks_like_noise_utterance("Oh.")
    assert looks_like_noise_utterance("um")
    assert not looks_like_noise_utterance("ရပ်ပါ")
    assert not looks_like_noise_utterance("သီချင်း ဖွင့်")


def test_should_dispatch_gates_empty_and_noise() -> None:
    assert not should_dispatch_otto_tool("self.otto.stop", "")
    assert not should_dispatch_otto_tool("self.otto.stop", "Oh.")
    assert should_dispatch_otto_tool("self.otto.stop", "ရပ်")
    assert should_dispatch_otto_tool("self.otto.stop", "hello", device_moving=True)
    assert not should_dispatch_otto_tool("self.otto.stop", "hello", device_moving=False)
    assert not should_dispatch_otto_tool("self.otto.action", "")
    assert not should_dispatch_otto_tool("self.otto.action", "Oh.")
    assert should_dispatch_otto_tool("self.otto.action", "walk forward")
    # Gemini may call action correctly even when STT is garbled (no motion keyword).
    assert should_dispatch_otto_tool("self.otto.action", "ကလေး နဲ့ မိစေကွာ")
    assert should_dispatch_otto_tool("self.otto.action", "hello please")
    assert should_dispatch_otto_tool("self.otto.servo_sequences", "နည်းနည်း လှုပ်ပေး")
    assert not should_dispatch_otto_tool("self.otto.servo_sequences", "")


def test_parse_otto_moving() -> None:
    assert parse_otto_moving("moving") is True
    assert parse_otto_moving("idle") is False
    assert parse_otto_moving("") is False
