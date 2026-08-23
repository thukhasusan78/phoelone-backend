#!/usr/bin/env python3
"""Isolated Gemini Native Audio TTS sandbox — does NOT import from app/.

Reads GEMINI_API_KEYS from the project-root .env (never prints the key).
Uses generate_content TTS (not Live conversational audio) so a fixed
Burmese line can be A/B tested.

Edit the TUNING knobs below, then run:
  .venv/bin/python voicetype_test/test_gemini_audio.py
  .venv/bin/python voicetype_test/test_gemini_audio.py --play

Docs: https://ai.google.dev/gemini-api/docs/speech-generation
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

# =============================================================================
# TUNING — edit these and re-run
# =============================================================================

# TTS models (Native Audio TTS — NOT Live). Prefer 3.1 for streaming/tags.
MODEL = "gemini-3.1-flash-tts-preview"
# MODEL = "gemini-2.5-flash-preview-tts"  # fallback if 3.1 unavailable

# Prebuilt voices — pick youthful/bright for "cute EMO robot"
# Full list: Zephyr, Puck, Charon, Kore, Fenrir, Leda, Orus, Aoede, ...
VOICE = "Zephyr"  # Youthful
# VOICE = "Achernar"   # Soft
# VOICE = "Puck"       # Upbeat
# VOICE = "Zephyr"     # Bright
# VOICE = "Laomedeia"  # Upbeat
# VOICE = "Sadachbia"  # Lively

# Optional ISO 639-1; Burmese = "my". Leave "" to rely on auto-detect.
LANGUAGE_CODE = "my"

# Persona / style (Gemini has NO numeric pitch/rate — steer with language)
STYLE = (
    "Cute, youthful EMO-like companion robot. Bright vocal smile, slightly "
    "higher register, playful and warm — not creepy, not deep, not formal."
)
DIRECTOR_NOTES = """\
Style: Cute EMO robot companion — bright, friendly, slightly chirpy.
Pacing: Energetic but clear; short sentences with a bounce.
Accent: Natural Burmese (Myanmar). Do not add English words.
Breathing: Light and airy; never gravelly or adult-news-anchor.
"""

# English audio tags (recommended even for non-English transcripts)
AUDIO_TAGS = "[speak in a high-pitched cute robot voice]"  # try: [giggles], [curious], [softly], "" to disable

# Sample Burmese line (same as Edge script for A/B listening)
TEXT = "မင်္ဂလာပါ။ ကျွန်တော် ဖိုးလုန်း ပါ။"

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
ESP32_SAMPLE_RATE = 24000  # Gemini TTS PCM is typically 24 kHz; matches downlink
MAX_RETRIES = 2

# =============================================================================


def load_gemini_api_key(env_path: Path = ENV_PATH) -> str:
    """Parse GEMINI_API_KEYS from .env (comma-separated); return first key."""
    if not env_path.is_file():
        raise FileNotFoundError(f"Missing env file: {env_path}")
    raw_value = ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip().upper() != "GEMINI_API_KEYS":
            continue
        raw_value = value.strip().strip("'").strip('"')
        break
    if not raw_value:
        raise RuntimeError("GEMINI_API_KEYS not set in .env")
    first = raw_value.split(",")[0].strip()
    if not first:
        raise RuntimeError("GEMINI_API_KEYS is empty")
    return first


def build_prompt(text: str, tags: str) -> str:
    """Director-style prompt so the model recites TEXT instead of reading notes."""
    tagged = f"{tags} {text}".strip() if tags else text
    return f"""# AUDIO PROFILE: Phoe Lone
## "Cute EMO Robot Companion"

## THE SCENE: Small desktop robot speaker
A friendly home robot with a soft LED face. The listener is nearby.
Speak only the transcript below — never read the director notes aloud.

### DIRECTOR'S NOTES
{DIRECTOR_NOTES.strip()}
Overall style: {STYLE}

#### TRANSCRIPT
{tagged}
"""


def extract_pcm(response: types.GenerateContentResponse) -> bytes:
    """Pull inline PCM audio bytes from a generate_content response."""
    if not response.candidates:
        raise RuntimeError("no candidates in response")
    parts = response.candidates[0].content.parts if response.candidates[0].content else None
    if not parts:
        raise RuntimeError("empty content parts")
    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline is None:
            continue
        data = getattr(inline, "data", None)
        if data:
            return bytes(data)
    raise RuntimeError("no inline audio data in response")


def write_wav(path: Path, pcm: bytes, rate: int = ESP32_SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def try_play(path: Path) -> None:
    ffplay = shutil.which("ffplay")
    if not ffplay:
        print(f"  (ffplay not found — open {path} manually)")
        return
    subprocess.run(
        [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
        check=False,
    )


def synthesize(client: genai.Client, prompt: str) -> bytes:
    speech = types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
        ),
    )
    if LANGUAGE_CODE:
        speech.language_code = LANGUAGE_CODE

    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=speech,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=config,
            )
            return extract_pcm(response)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"  attempt {attempt}/{MAX_RETRIES} failed: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"Gemini TTS failed after retries: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated Gemini Native Audio TTS tuner")
    parser.add_argument(
        "--play",
        action="store_true",
        help="Play output with ffplay if available",
    )
    args = parser.parse_args()

    if not TEXT.strip():
        print("TEXT is empty — set a Burmese sample at the top of the script.", file=sys.stderr)
        return 1

    try:
        api_key = load_gemini_api_key()
    except Exception as exc:  # noqa: BLE001
        print(f"API key load failed: {exc}", file=sys.stderr)
        return 1

    prompt = build_prompt(TEXT, AUDIO_TAGS)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_voice = "".join(c if c.isalnum() or c in "-_" else "_" for c in VOICE)
    out_path = OUTPUT_DIR / f"gemini_{safe_voice}_{stamp}.wav"

    print("=== Gemini Native Audio TTS ===")
    print(f"  model={MODEL}")
    print(f"  voice={VOICE}  language_code={LANGUAGE_CODE or '(auto)'}")
    print(f"  audio_tags={AUDIO_TAGS!r}")
    print(f"  text={TEXT!r}")
    print(f"  style={STYLE[:80]}...")

    client = genai.Client(api_key=api_key)
    pcm = synthesize(client, prompt)
    write_wav(out_path, pcm)
    print(f"  wrote {out_path} ({len(pcm)} bytes PCM, {ESP32_SAMPLE_RATE} Hz mono)")

    if args.play:
        try_play(out_path)

    print("\nDone. Compare with Edge outputs in:", OUTPUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
