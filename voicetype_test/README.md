# Voice type test sandbox

Isolated scripts for tuning a **cute, EMO-like Burmese robot voice**.  
They do **not** import from or modify `app/`.

## Quick start

From the repo root, with the project venv:

```bash
# Edge TTS — runs several pitch/rate presets by default
.venv/bin/python voicetype_test/test_edge_tts.py

# Gemini Native Audio TTS — needs GEMINI_API_KEYS in ../.env
.venv/bin/python voicetype_test/test_gemini_audio.py

# Optional: play each file (requires ffplay)
.venv/bin/python voicetype_test/test_edge_tts.py --play
.venv/bin/python voicetype_test/test_gemini_audio.py --play

# Edge: use only the single VOICE/RATE/PITCH knobs (ignore PRESETS)
.venv/bin/python voicetype_test/test_edge_tts.py --single
```

Outputs land in `voicetype_test/outputs/` (gitignored).

Edit knobs at the **top** of each script, re-run, listen, repeat.

| Script | What you tweak |
|--------|----------------|
| `test_edge_tts.py` | `VOICE`, `RATE`, `PITCH`, `VOLUME`, `TEXT`, `PRESETS` |
| `test_gemini_audio.py` | `MODEL`, `VOICE`, `LANGUAGE_CODE`, `STYLE`, `DIRECTOR_NOTES`, `AUDIO_TAGS`, `TEXT` |

Gemini has **no** numeric pitch/rate — style is steered with director notes and English audio tags (`[excited]`, `[giggles]`, …).

---

## Advisory: cute EMO-like Burmese voice for ESP32

### What production does today

- **Edge TTS** speaks Gemini’s Burmese transcript (`my-MM-NilarNeural`, `rate=+8%`, `pitch=+12Hz`).
- **Gemini Live** native audio is generated then **discarded** (STT/LLM only).

### Best methods (ranked for this robot)

1. **Tune Edge first (fastest, zero architecture change)**  
   Only two real Burmese neural voices: **Nilar** (female, cute candidate) and **Thiha** (male).  
   Microsoft exposes only `rate` / `pitch` / `volume` — no style SSML for `my-MM`.  
   Try Nilar around `+20Hz`–`+36Hz` and `+10%`–`+15%` before swapping engines.  
   Too much pitch shift flattens Burmese register and hurts intelligibility on a small speaker.

2. **Gemini Native Audio TTS is the best expressive option**  
   Model: `gemini-3.1-flash-tts-preview` (or `gemini-2.5-flash-preview-tts`).  
   Burmese is supported (`my` / Cloud `my-MM` preview).  
   Steer “cute / youthful / slightly robotic EMO” with director notes + tags, and youthful voices (`Leda`, `Achernar`, `Puck`, `Zephyr`).  
   Output is **24 kHz PCM**, matching ESP32 downlink — no MP3→ffmpeg hop.  
   Tradeoffs: preview flakes (500s), higher latency/cost than Edge, quality drift on long text (keep chunking ~180 chars).

3. **Do not use Gemini Live native audio as the TTS experiment**  
   Live invents wording; it cannot reliably recite a fixed line for A/B listening.  
   Keep Live for STT/LLM as today.

4. **Optional DSP after Edge only if Gemini is too slow**  
   `ffmpeg` `asetrate`/`atempo` or Rubber Band (formant-preserving) can fake a smaller robot.  
   Risky on Burmese — last resort, not primary.

5. **Skip for this project**  
   On-device ESP32 TTS (too thin), ElevenLabs (weak Burmese), RVC/voice-conversion (too heavy for live turns).

### Suggested experiment order

1. Run both scripts on the **same** Burmese sentence.  
2. Pick the least-weird cute Nilar Edge preset vs Gemini `Leda`/`Puck` with a cute-robot director prompt.  
3. Only then consider swapping production Edge for Gemini TTS (leave `app/` alone until you decide).

### Docs

- [Edge TTS](https://github.com/rany2/edge-tts) — `--rate`, `--pitch`, `--volume`
- [Gemini speech generation](https://ai.google.dev/gemini-api/docs/speech-generation) — voices, tags, Burmese
