from __future__ import annotations

import re
import unicodedata

BURMESE_END = re.compile(r"(။|\n+)")
FALLBACK_BURMESE = "ဆောရီးပါ၊ ကျွန်တော် နားမလည်သေးပါဘူး။ ခဏနေမှ ထပ်ပြောပေးပါ။"

# Gemini native-audio leaks control tokens like <ctrl46>; also SSML/HTML/markdown.
_TAG_RE = re.compile(r"</?[^>\s][^>]{0,200}>")
_UNCLOSED_TAG_RE = re.compile(r"</?[A-Za-z][\w:.-]{0,40}")
_MARKDOWN_RE = re.compile(r"[*_`#~]+")
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "\U00002300-\U000023FF"
    "]+"
)
_SPEAKABLE_PUNCT = set("။၊.,!?…:;、-—'\"()[] \t")
# Gemini often inserts the Latin robot name; keep it speakable in Burmese.
_LATIN_NAME_RE = re.compile(r"\b(?:phoe|pho|foe)\s*[-_]?lone\b", re.IGNORECASE)
_BURMESE_NAME = "ဖိုးလုန်း"
# Gemini often prepends tool-result JSON to the spoken sentence.
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}")
_JSON_KV_RE = re.compile(
    r'["\']?[A-Za-z_][\w]*["\']?\s*:\s*'
    r'(?:null|true|false|-?\d+(?:\.\d+)?|["\'][^"\']{0,80}["\'])\s*,?',
    re.IGNORECASE,
)


def _strip_json_payloads(text: str) -> str:
    """Drop tool-result JSON so Edge TTS never reads ok/action/null aloud."""
    cleaned = text
    for _ in range(8):
        nxt = _JSON_OBJECT_RE.sub(" ", cleaned)
        if nxt == cleaned:
            break
        cleaned = nxt
    cleaned = _JSON_KV_RE.sub(" ", cleaned)
    return cleaned


def sanitize_for_tts(text: str | None) -> str:
    """Strip non-speech junk; keep Burmese, digits, and ASCII letters for Edge TTS."""
    if not text:
        return ""
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = _strip_json_payloads(cleaned)
    cleaned = _LATIN_NAME_RE.sub(_BURMESE_NAME, cleaned)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = _UNCLOSED_TAG_RE.sub(" ", cleaned)
    cleaned = _MARKDOWN_RE.sub(" ", cleaned)
    cleaned = _EMOJI_RE.sub(" ", cleaned)
    kept: list[str] = []
    for char in cleaned:
        code = ord(char)
        if char.isspace() or char.isdigit() or char in _SPEAKABLE_PUNCT:
            kept.append(" " if char.isspace() else char)
            continue
        if char.isascii() and char.isalpha():
            kept.append(char)
            continue
        if (
            0x1000 <= code <= 0x109F
            or 0xA9E0 <= code <= 0xA9FF
            or 0xAA60 <= code <= 0xAA7F
        ):
            kept.append(char)
    cleaned = " ".join("".join(kept).split()).strip()
    return cleaned


def completed_burmese_sentences(text: str, max_chars: int = 180) -> list[str]:
    """Sentences that already end with ။ — safe to speak before the turn finishes."""
    return [chunk for chunk in chunk_burmese(text, max_chars=max_chars) if chunk.endswith("။")]


def chunk_burmese(text: str, max_chars: int = 180) -> list[str]:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return []
    pieces: list[str] = []
    buf = ""
    for part in BURMESE_END.split(cleaned):
        if not part:
            continue
        if part == "။":
            buf += part
            if buf.strip():
                pieces.append(buf.strip())
            buf = ""
            continue
        if part.strip() == "":
            if buf.strip():
                pieces.append(buf.strip())
            buf = ""
            continue
        if len(buf) + len(part) > max_chars and buf.strip():
            pieces.append(buf.strip())
            buf = part
        else:
            buf += part
    if buf.strip():
        pieces.append(buf.strip())
    return pieces or [cleaned[:max_chars]]


def cap_text(text: str, max_chars: int) -> str:
    cleaned = " ".join((text or "").split()).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"
