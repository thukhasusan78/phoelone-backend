from __future__ import annotations

from app.audio.text import (
    FALLBACK_BURMESE,
    cap_text,
    chunk_burmese,
    completed_burmese_sentences,
    sanitize_for_tts,
)


def test_chunk_burmese_sentences() -> None:
    text = "မင်္ဂလာပါ။ ကျွန်တော် ဖိုးလုန်း ပါ။"
    chunks = chunk_burmese(text)
    assert len(chunks) == 2
    assert chunks[0].endswith("။")


def test_chunk_empty() -> None:
    assert chunk_burmese("   ") == []


def test_completed_burmese_sentences_skips_trailing_fragment() -> None:
    text = "မင်္ဂလာပါ။ ကျွန်တော် ဖိုးလုန်း ပါ။ ဟယ်လို"
    assert completed_burmese_sentences(text) == [
        "မင်္ဂလာပါ။",
        "ကျွန်တော် ဖိုးလုန်း ပါ။",
    ]
    assert completed_burmese_sentences("ဟယ်လို") == []
    assert completed_burmese_sentences("") == []


def test_cap_text() -> None:
    assert cap_text("hello", 10) == "hello"
    assert cap_text("abcdefghijklmnop", 8).endswith("…")


def test_sanitize_strips_ctrl_tags_markdown_and_emoji() -> None:
    raw = "**မင်္ဂလာပါ** 😊 <ctrl46> *robot*"
    assert sanitize_for_tts(raw) == "မင်္ဂလာပါ robot"


def test_sanitize_drops_noise_languages() -> None:
    assert sanitize_for_tts("สวัสดีครับ") == ""
    assert sanitize_for_tts("హలో") == ""
    assert sanitize_for_tts("   ") == ""


def test_sanitize_keeps_english_alphanumeric() -> None:
    assert sanitize_for_tts("hello world") == "hello world"
    assert sanitize_for_tts("WiFi API") == "WiFi API"
    assert sanitize_for_tts("မင်္ဂလာပါ WiFi") == "မင်္ဂလာပါ WiFi"


def test_sanitize_keeps_burmese_when_mixed() -> None:
    mixed = "สวัสดี " + "မင်္ဂလာပါ" + " <div>హలో</div> ***"
    assert sanitize_for_tts(mixed) == "မင်္ဂလာပါ"


def test_sanitize_preserves_fallback() -> None:
    assert sanitize_for_tts(FALLBACK_BURMESE) == FALLBACK_BURMESE


def test_sanitize_rewrites_latin_robot_name() -> None:
    raw = "မင်္ဂလာပါ! Phoe Lone ပါ။ ဘာလုပ်ပေးရမလဲ?"
    assert sanitize_for_tts(raw) == "မင်္ဂလာပါ! Mickey ပါ။ ဘာလုပ်ပေးရမလဲ?"
    assert sanitize_for_tts("ကျွန်တော် ဖိုးလုန်း ပါ။") == "ကျွန်တော် Mickey ပါ။"


def test_sanitize_strips_tool_json_before_speech() -> None:
    raw = (
        '{"ok": true, "action": "swing", "amount": null, "direction": null, '
        '"speed": 500, "steps": 5}ဖိုးလုန်း ကပြနေမယ်နော်။'
    )
    assert sanitize_for_tts(raw) == "Mickey ကပြနေမယ်နော်။"
    leaked = '"ok": true, "action": "swing", "amount": null ဖိုးလုန်း ကပြနေမယ်နော်။'
    assert sanitize_for_tts(leaked) == "Mickey ကပြနေမယ်နော်။"
    assert "ok" not in sanitize_for_tts(raw).casefold()
    assert "swing" not in sanitize_for_tts(raw).casefold()
