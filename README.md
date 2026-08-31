# Phoe Lone FastAPI backend

XiaoZhi-compatible brain for the ESP32 Mickey client ([thukhasusan78/phoelone](https://github.com/thukhasusan78/phoelone), board type `mickey`). Legacy OTA identities `otto-robot` and `phoe-lone` are still accepted.

The device posts to `/xiaozhi/ota/`, receives a WebSocket URL, then speaks over `wss://…/xiaozhi/v1/`. MQTT/UDP is omitted from OTA JSON so the robot stays on the WebSocket voice path. Camera boards may POST `/vision/explain/` (stub until a camera is wired).

The XiaoZhi **wire protocol** (hello, listen, TTS, MCP) is in [backend_spec.md](backend_spec.md). Servo-hold firmware patches historically lived in [firmware/otto-robot/README.md](firmware/otto-robot/README.md); live code is `main/boards/mickey/` on the firmware repo.

## Architecture

```
ESP32  --OTA-->  FastAPI /xiaozhi/ota/
ESP32  --WS--->  DeviceSession
                   ├─ Opus decode → Silero VAD → Gemini Live (STT + tools + Burmese text)
                   ├─ Edge TTS → 24 kHz Opus downlink
                   ├─ Host tools (weather, news, web, music, datetime, email)
                   └─ Device MCP (walk, stop, volume, face, …)
```

1. **Auth:** opaque WebSocket tokens after OTA. Unknown devices get a 6-digit activation code (portal at `/`) and cannot open WebSocket until bound. Identity is `Device-Id` + `Client-Id` + bearer over TLS. A successful activate sets a signed `companion` cookie and opens the dashboard (presence, dance pad, Rock-Paper-Scissors). The browser talks `wss://…/companion/v1/`; it never sees the device bearer. Mickey must be awake (device `/xiaozhi/v1/` open) for body reactions. Optional `COMPANION_PIN` unlocks the dashboard from another browser.
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

Point firmware `CONFIG_OTA_URL` at `https://phoelone.thukha.online/xiaozhi/ota/`. Live Mickey builds bake that URL in `main/boards/mickey/config.json` (`board.type` = `mickey`).

New robots POST OTA, show a 6-digit code, and stay pending until that code is entered at [https://phoelone.thukha.online/](https://phoelone.thukha.online/). WebSocket is rejected until then.

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

Pre-provision with `phoe-lone provision` if you disable auto-activation. With `ALLOW_AUTO_PROVISION=true`, unknown robots get a 6-digit code at https://phoelone.thukha.online/.

## Production (this VPS: native systemd)

**Architecture (chosen):** PostgreSQL + Redis + Caddy + a **single** uvicorn worker, all on the host via systemd. Docker is **not** used here: the droplet is 1 vCPU / 2 GB, Compose is not installed, and in-memory WebSocket sessions cannot be multi-process anyway.

Do **not** run `alembic` against hostname `postgres` on the host — that name exists only on a Compose network. Native migrations read `DATABASE_URL` from `.env` (`127.0.0.1`).

**Swap:** keep the existing **2 GB** `/swapfile`. Do not add a second swap file. `vm.swappiness=10` so the kernel prefers RAM until it is actually tight (TTS/ffmpeg spikes can still use swap).

1. DNS A record `phoelone.thukha.online` → this droplet.
2. `.env` (never commit it):
   - `ENVIRONMENT=production`
   - `HOST=127.0.0.1` (uvicorn is loopback-only; Caddy owns 80/443)
   - `DATABASE_URL=postgresql+asyncpg://phoe:<password>@127.0.0.1:5432/phoe_lone`
   - `REDIS_URL=redis://127.0.0.1:6379/0`
   - `PUBLIC_HTTP_ORIGIN=https://phoelone.thukha.online`
   - `PUBLIC_WS_ORIGIN=wss://phoelone.thukha.online`
   - `AUTH_PEPPER`, `POSTGRES_PASSWORD`, `ACME_EMAIL`, `GEMINI_API_KEYS`
   - `ALLOW_AUTO_PROVISION=true`
   - `MAX_CONCURRENT_SESSIONS=4`
3. Install/start: `postgresql`, `redis-server`, `caddy`, unit `phoe-lone.service`.
4. Migrate: `scripts/migrate.sh` (venv Alembic → localhost Postgres).
5. Activate robots at `https://phoelone.thukha.online/` or:

```bash
cd /root/phoe_lone_server
.venv/bin/phoe-lone provision --device-id aa:bb:cc:dd:ee:ff --client-id <nvs-uuid>
```

6. Firmware `CONFIG_OTA_URL` = `https://phoelone.thukha.online/xiaozhi/ota/`.

**Ops:** `systemctl status phoe-lone caddy postgresql redis-server`. Logs: `journalctl -u phoe-lone -f`. Health: `curl -fsS https://phoelone.thukha.online/health`. After editing `Caddyfile`: `scripts/reload-caddy.sh`. Backups: `scripts/backup.sh` (also nightly cron 03:15 UTC). Firewall: UFW allows 22/80/443 only; uvicorn is loopback.

**Smoke:** hello within 10 s; Burmese STT then TTS; walk / stop; weather; local mp3 playback; abort cuts TTS.

Optional Docker Compose remains in `compose.yaml` for a larger host only (`API_UPSTREAM=api:8000`). Do not run Compose and systemd uvicorn at the same time.
