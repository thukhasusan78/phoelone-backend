#!/usr/bin/env python3
"""Isolated Edge TTS sandbox — does NOT import from app/.

Edit the TUNING knobs below, then run:
  .venv/bin/python voicetype_test/test_edge_tts.py
  .venv/bin/python voicetype_test/test_edge_tts.py --play
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import edge_tts

# =============================================================================
# TUNING — edit these and re-run
# =============================================================================

# Burmese neural voices (Microsoft Edge / Azure). Only two exist.
VOICE = "my-MM-NilarNeural"  # female — best cute candidate
FALLBACK_VOICE = "my-MM-ThihaNeural"  # male

# Prosody (Edge Communicate API). Formats: rate "+8%", pitch "+12Hz", volume "+0%"
RATE = "+45%"  # production default; try "+12%", "+15%"
PITCH = "+100Hz"  # production default; try "+20Hz", "+28Hz", "+36Hz"
VOLUME = "+100%"

# Sample Burmese line (same as Gemini script for A/B listening)
TEXT = "မင်္ဂလာပါ။ ကျွန်တော် ဖိုးလုန်း ပါ။"

# If non-empty, run every preset instead of the single VOICE/RATE/PITCH above.
# Comment out or set to [] to use only the knobs above.
PRESETS: list[dict[str, str]] = [
    {"name": "prod", "voice": "my-MM-ThihaNeural", "rate": "+8%", "pitch": "+12Hz"},
    {"name": "cute_mild", "voice": "my-MM-ThihaNeural", "rate": "+10%", "pitch": "+20Hz"},
    {"name": "cute_med", "voice": "my-MM-ThihaNeural", "rate": "+12%", "pitch": "+28Hz"},
    {"name": "cute_high", "voice": "my-MM-ThihaNeural", "rate": "+15%", "pitch": "+36Hz"},
    # {"name": "thiha", "voice": "my-MM-ThihaNeural", "rate": "+8%", "pitch": "+12Hz"},
]

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
ESP32_SAMPLE_RATE = 24000  # matches app downlink (Opus 24 kHz)

# =============================================================================


async def synthesize_mp3(
    text: str,
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
) -> bytes:
    communicate = edge_tts.Communicate(
        text,
        voice=voice,
        rate=rate,
        pitch=pitch,
        volume=volume,
    )
    chunks: list[bytes] = []
    async for message in communicate.stream():
        if message["type"] == "audio":
            chunks.append(message["data"])
    if not chunks:
        raise RuntimeError(f"edge-tts returned no audio for voice={voice!r}")
    return b"".join(chunks)


def mp3_to_wav24k(mp3_path: Path, wav_path: Path) -> bool:
    """Convert MP3 → 24 kHz mono PCM WAV if ffmpeg is available."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(mp3_path),
            "-ac",
            "1",
            "-ar",
            str(ESP32_SAMPLE_RATE),
            "-sample_fmt",
            "s16",
            str(wav_path),
        ],
        check=True,
        capture_output=True,
    )
    return True


def try_play(path: Path) -> None:
    ffplay = shutil.which("ffplay")
    if not ffplay:
        print(f"  (ffplay not found — open {path} manually)")
        return
    subprocess.run(
        [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
        check=False,
    )


async def run_one(
    *,
    name: str,
    text: str,
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
    play: bool,
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    mp3_path = OUTPUT_DIR / f"edge_{safe}_{stamp}.mp3"

    print(f"\n=== Edge TTS preset={name!r} ===")
    print(f"  voice={voice}  rate={rate}  pitch={pitch}  volume={volume}")
    print(f"  text={text!r}")

    voices = [voice]
    if FALLBACK_VOICE and FALLBACK_VOICE != voice:
        voices.append(FALLBACK_VOICE)

    last_error: Exception | None = None
    mp3: bytes | None = None
    used_voice = voice
    for candidate in voices:
        try:
            mp3 = await synthesize_mp3(text, candidate, rate, pitch, volume)
            used_voice = candidate
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"  voice failed: {candidate} → {exc}")
    if mp3 is None:
        raise RuntimeError(f"all voices failed: {last_error}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    mp3_path.write_bytes(mp3)
    print(f"  wrote {mp3_path} ({len(mp3)} bytes, voice={used_voice})")

    wav_path = mp3_path.with_suffix(".wav")
    try:
        if mp3_to_wav24k(mp3_path, wav_path):
            print(f"  wrote {wav_path} ({ESP32_SAMPLE_RATE} Hz mono)")
            if play:
                try_play(wav_path)
        elif play:
            try_play(mp3_path)
    except subprocess.CalledProcessError as exc:
        print(f"  ffmpeg convert failed: {exc}")
        if play:
            try_play(mp3_path)

    return mp3_path


async def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated Edge TTS voice tuner")
    parser.add_argument(
        "--play",
        action="store_true",
        help="Play each output with ffplay if available",
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="Ignore PRESETS; use only VOICE/RATE/PITCH knobs",
    )
    args = parser.parse_args()

    if not TEXT.strip():
        print("TEXT is empty — set a Burmese sample at the top of the script.", file=sys.stderr)
        return 1

    jobs: list[dict[str, str]]
    if args.single or not PRESETS:
        jobs = [
            {
                "name": "manual",
                "voice": VOICE,
                "rate": RATE,
                "pitch": PITCH,
            }
        ]
    else:
        jobs = PRESETS

    for job in jobs:
        await run_one(
            name=job["name"],
            text=TEXT,
            voice=job.get("voice", VOICE),
            rate=job.get("rate", RATE),
            pitch=job.get("pitch", PITCH),
            volume=VOLUME,
            play=args.play,
        )

    print("\nDone. Compare files in:", OUTPUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
