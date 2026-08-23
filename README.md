# Phoe Lone FastAPI backend

XiaoZhi-compatible brain for the ESP32 Phoe Lone / otto-robot client.

The device posts to `/xiaozhi/ota/`, receives a WebSocket URL, then speaks over `wss://…/xiaozhi/v1/`. MQTT/UDP is omitted from OTA JSON so the robot stays on the WebSocket voice path. Camera boards may POST `/vision/explain/` (stub until a camera is wired).

The XiaoZhi **wire protocol** (hello, listen, TTS, MCP) is in [backend_spec.md](backend_spec.md). Servo-hold firmware patches are in [firmware/otto-robot/README.md](firmware/otto-robot/README.md).

## Architecture

```
ESP32  --OTA-->  FastAPI /xiaozhi/ota/
ESP32  --WS--->  DeviceSession
                   ├─ Opus decode → Silero VAD → Gemini Live (STT + tools + Burmese text)
                   ├─ Edge TTS → 24 kHz Opus downlink
                   ├─ Host tools (weather, news, web, music, datetime, email)
                   └─ Device MCP (walk, stop, volume, face, …)
```

1. **Auth:** opaque WebSocket tokens after OTA. Identity is `Device-Id` + `Client-Id` + bearer over TLS (not hardware attestation).
2. **Server Silero VAD:** in auto/wake-word mode the ESP32 often does **not** send `listen/stop`. The server endpoints on non-speech (`VAD_MIN_SILENCE_MS`, default 800 ms) or `MAX_FORWARDED_AUDIO_SECONDS`, then sends `tts/start` so the device leaves listening.
3. **Gemini Live** (`gemini-3.1-flash-live-preview`): gated 16 kHz PCM with manual `activity_start` / `activity_end`. Native response audio is discarded; output transcription is Burmese Unicode only.
4. **Edge TTS:** `my-MM-NilarNeural` (fallback Thiha). Downlink Opus is paced (5-frame burst, then 60 ms/frame) so the ESP32 1.2 s decode queue does not drop mid-sentence audio.
5. **Chat context:** not stored in Postgres, Redis, or on the device. One Gemini Live socket per device WebSocket holds Google-side context (plus an in-RAM resumption handle on reconnect). A new process or new device session starts empty. When a song ends, the server sends Gemini an INTERNAL EVENT (`send_client_content`) with title/artist/status so the Live session knows playback stopped; a short Burmese wrap-up may be spoken before `tts/stop`.
6. **Postgres** stores device records and tokens only. **Redis** caches some host-tool JSON (not `search_music`).

## Quick start (development)

```bash
cp .env.example .env
# set GEMINI_API_KEYS, AUTH_PEPPER, ALLOW_AUTO_PROVISION=true
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Point firmware `CONFIG_OTA_URL` at `http://<this-host>:8000/xiaozhi/ota/`.

## Local music

Drop files into `data/local_music/` named `Artist - Title.mp3` (or `.wav` / `.m4a` / `.ogg`). The server scans the folder (no JSON edit required). Spoken names must appear in the filename.

- **Generic** (“play a song”, “သီချင်း ဖွင့်ပါ”, Myanmar music): random **local** track only. No iTunes / YouTube.
- **Specific** (artist or title): score local files first; remote providers only if there is no local hit.
- Playback is FFmpeg → Opus on the existing TTS WebSocket path. `MUSIC_MAX_SECONDS=0` plays to the end.
- Optional `MUSIC_CATALOG_PATH` JSON is an HTTPS overlay only. yt-dlp is last-resort for named tracks (`MUSIC_YTDLP_ENABLED`).

Do not commit the audio files. Keep `data/local_music/.gitkeep`.

## Protocol notes

- OTA: `GET` and `POST` on `/xiaozhi/ota` and `/xiaozhi/ota/` (no slash redirect).
- WebSocket: `/xiaozhi/v1` and `/xiaozhi/v1/`.
- Uplink: raw Opus, 16 kHz, mono, 60 ms. Downlink: Opus, 24 kHz, mono, 60 ms, only after `tts/start`.
- VAD: `VAD_BACKEND`, `VAD_SPEECH_THRESHOLD`, `VAD_MIN_SPEECH_MS`, `VAD_MIN_SILENCE_MS`, `VAD_PREROLL_CHUNKS`, `MAX_FORWARDED_AUDIO_SECONDS`.

Pre-provision devices, rotate tokens, and keep `ALLOW_AUTO_PROVISION=false` in production.

## Production (DigitalOcean)

Compose stack: Caddy (80/443 TLS) → FastAPI; PostgreSQL and Redis on the private network only.

1. Droplet (2 vCPU / 2 GB or larger), domain A record, Docker + Compose.
2. Clone, copy `.env.example` to `.env`, set:
   - `DOMAIN`, `ACME_EMAIL`
   - `PUBLIC_HTTP_ORIGIN=https://<domain>`
   - `PUBLIC_WS_ORIGIN=wss://<domain>`
   - `DATABASE_URL` / `POSTGRES_PASSWORD`
   - `AUTH_PEPPER` (long random)
   - `GEMINI_API_KEYS`
   - `TAVILY_KEY` (optional)
   - `ALLOW_AUTO_PROVISION=false`
   - `METRICS_TOKEN`
   - `MUSIC_LOCAL_DIR=data/local_music`, `MUSIC_MAX_SECONDS=0`
3. `docker compose run --rm migrate` then `docker compose up -d`
4. Provision each robot:

```bash
docker compose exec api phoe-lone provision \
  --device-id aa:bb:cc:dd:ee:ff \
  --client-id <nvs-uuid>
```

5. Device NVS: **only** `CONFIG_OTA_URL` / `wifi.ota_url` = `https://<domain>/xiaozhi/ota/`.

Firmware dummy URL returns 404 (`FIRMWARE_VERSION=0.0.0`) so the robot skips upgrades.

**Ops:** `GET /health`, `GET /ready`, `GET /metrics` (Bearer `$METRICS_TOKEN`). Logs: `docker compose logs -f api`. Backups: `scripts/backup.sh`. Token rotate / disable: `phoe-lone rotate` / `phoe-lone disable`. Firewall: 22, 80, 443 only.

**Smoke:** hello within 10 s; Burmese STT then TTS; walk / stop; weather; drop an mp3 in `data/local_music/` and say “play a song” (full length); abort cuts TTS; idle pings keep the 120 s device timeout from firing.

Resource limits are in `compose.yaml`. Redis uses `allkeys-lru` at 128 MB.
