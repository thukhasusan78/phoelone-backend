# Phoe Lone Backend Production Plan (Python / FastAPI)

**Status:** P0.5 / P0.6 / P0.S5 / P0.S6 implemented in the Python app (2026-08-25). Remaining P0/P1/P2 boxes below are still open.  
**Date:** 2026-08-25  
**Repo:** this VPS tree (`phoe_lone_server`).  
**Companion:** ESP32 work lives in `CLIENT_PRODUCTION_PLAN.md` on the PC firmware repo. This file never assigns C++ or GPIO tasks.  
**Invariant:** Silero VAD, Gemini Live STT, Edge TTS, local music, Otto MCP motion gating, and the existing hello/listen/abort path must keep working.

The robot firmware is developed **locally**. Treat the JSON/MCP shapes in §3 as a frozen wire contract. If firmware lags (still `wired:false`, no `pong`), the server must stay compatible: ignore unknown device fields, do not require notifications, do not fail hello.

---

## 0. How to use this file on the VPS

| Section | Use when |
|---------|----------|
| [1. Server current state](#1-server-current-state) | Orienting a new session |
| [2. Ping loop and inbound pong](#2-ping-loop-and-inbound-pong) | Keepalive |
| [3. Wire contracts (server view)](#3-wire-contracts-server-view) | JSON you send/receive |
| [4. MCP sensor notifications](#4-mcp-sensor-notifications) | `notifications/phoe_lone.event` |
| [5. Gemini Live injection](#5-gemini-live-injection) | Pet / pickup while listening |
| [6. Catalog, prompts, tools](#6-catalog-prompts-tools) | `wired:true` era |
| [7. Server OTA endpoint](#7-server-ota-endpoint) | `/xiaozhi/ota/` |
| [8. Session / WS stability](#8-session--ws-stability) | Timeouts, Caddy, Uvicorn |
| [9. P0 / P1 / P2 checklists](#9-p0--p1--p2-checklists-python-codebase) | Tick boxes in backend PRs |
| [10. File map](#10-file-map) | Where to edit |

Stack: FastAPI + Starlette WebSocket, Uvicorn, Caddy TLS, Postgres, Redis. MQTT is **not** advertised in OTA (`mqtt_enabled` stays false).

---

## 1. Server current state

### 1.1 What already works

- `GET`/`POST` `/xiaozhi/ota` and `/xiaozhi/ota/` (no slash redirect).
- WS `/xiaozhi/v1` and `/xiaozhi/v1/`: auth `Device-Id` + `Client-Id` + bearer; hello; Opus v1 16 kHz up / 24 kHz down.
- `DeviceSession`: listen/start/stop/detect, abort, MCP client (initialize, paginated `tools/list`, `tools/call`).
- Silero endpointing when the device never sends `listen/stop`; `tts/start` to leave listening.
- Gemini Live + host tools (weather, news, web, `search_music`, datetime, email) + device MCP via `ToolRouter`.
- Local music → FFmpeg → paced Opus on the TTS path.
- Keepalive **send**: every `keepalive_interval_s` (30) a JSON `type: ping`.
- Server idle close: `device_idle_timeout_s` (100) of no **inbound** frames, skipped in THINKING/SPEAKING.
- Listen idle: 8 s no uplink → end utterance.
- `/health`, `/ready`, `/metrics`, Compose + Caddy.
- Vision `POST /vision/explain` stub (`camera not available`).
- Dummy firmware `GET /firmware/none.bin` → **404**.

### 1.2 Gaps this plan owns

**Done (2026-08-25):**
- Inbound `type: pong` is a first-class type (`PongMessage`); no `session.unknown_type`.
- `McpClient.on_message` routes `notifications/*` to `DeviceSession._on_sensor_event`; `notifications/phoe_lone.event` is logged with `device_id`.
- Catalog + `SYSTEM_PROMPT` describe dual-fleet `wired:true` / `wired:false` sensors; never invent readings; fall → prefer stop.
- `prepare_ota` does **not** rotate the WS token on ordinary version checks. Wrapped `token_ciphertext` is stored at provision; OTA echoes the same bearer. Hash-only legacy rows get a one-time rotate + wrap (`auth.token_wrap_migrate`) — that single post-deploy OTA can disconnect an already-open WS.

**Still open:**
- OTA always `firmware.version = 0.0.0` and dummy URL. No board-channel selection, no signed URL, no `phoe-lone` identity.
- No Gemini INTERNAL EVENT for pet/pickup (P1.8).
- No proactive `alert` for battery/Wi-Fi (device MCP can be polled; nothing polls).
- `system` / `reboot` helper exists in `messages.py` and is never sent.
- Chat memory: Gemini Live socket + in-RAM resumption handle; dies with process/session.

### 1.3 Compatibility with local firmware

Until the PC firmware ships ping/pong and live sensors:

- Keep sending `type: ping` (already resets device 120 s timer even if logged unknown on device).
- Keep discovering `self.phoe_lone.*` stubs.
- Do not require `pong` or notifications for a session to be valid.

---

## 2. Ping loop and inbound pong

### 2.1 What you send today

`app/protocol/messages.py` → `keepalive()`:

```json
{ "session_id": "<uuid>", "type": "ping" }
```

`DeviceSession._keepalive_loop` (`session.py`):

- Sleep `keepalive_interval_s` (30).
- If LISTENING and no uplink for `listen_idle_timeout_s` (8): `_end_utterance_now("listen_idle")`.
- If socket not CONNECTED: `close()`.
- If no inbound for `device_idle_timeout_s` (100) **and** state not THINKING/SPEAKING: `close()`.
- Then `_queue_json(keepalive(session_id))` using **current** generation.

Writer drops queued items when `item.generation != state.generation`. Abort/listen bump generation → stale pings drop; next tick sends a fresh ping. Correct.

**Must keep pinging during SPEAKING / music.** The 100 s **server** idle is skipped, but the **device** 120 s last-incoming clock is not. Stopping ping during a 4-minute track drops the robot.

Tests: `tests/contract/test_websocket.py` `_recv_json` skips `type == ping`. Keep that; add pong coverage.

### 2.2 Why the device logs unknown type

Firmware `OnIncomingJson` does not handle `ping`. That is a **client** fix. Server contract: keep `type: ping` (do not switch to `alert`/`tts`/empty Opus).

Optional additive field (backward compatible; extra keys ignored on old firmware):

```json
{ "session_id": "<id>", "type": "ping", "ts_ms": 1710000000000 }
```

### 2.3 Inbound `pong` (P0 server)

Device (new firmware) may reply:

```json
{ "session_id": "<id>", "type": "pong", "ts_ms": <echo> }
```

Today `_on_json` treats `type == "pong"` as known (`PongMessage`). `_last_rx` is already updated for any message at the start of `_receive_loop`.

**Required:**

1. Parse `type == "pong"` as known. Update `_last_rx` (already updated for any message at the start of `_receive_loop` — good).
2. Do not treat pong as listen/abort/MCP.
3. Optional: record `last_pong_monotonic`; if JSON pings go out but no pong for N×30 s **while** the socket still receives uplink Opus, log `app_task_unhealthy` (application wedged, TCP alive). Do **not** kill the session in v1; metrics only.
4. Pydantic model `PongMessage` next to `AbortMessage` in `app/protocol/models.py` (`extra` allow).

### 2.4 Do not

- Send `alert` or `tts` as keepalive.
- Send binary silence as keepalive.
- Drop ping during music.
- Require pong before the firmware PR lands.

### 2.5 Transport-level WebSocket ping (ops + app)

ESP-IDF `esp_websocket_client` answers opcode **0x9 PING** with PONG in the stack ([docs](https://docs.espressif.com/projects/esp-protocols/esp_websocket_client/docs/latest/index.html)). That is independent of JSON `ping`.

**Uvicorn:** `--ws-ping-interval` / `--ws-ping-timeout` (defaults are often 20 s / 20 s). Confirm the Compose `command` or Caddy↔Uvicorn path actually passes these. If Caddy terminates WSS, ping may be Caddy↔Uvicorn only unless Caddy forwards to the device — **end-to-end opcode ping must reach the ESP32**. Prefer:

- Caddy `reverse_proxy` WebSocket without extra idle shorter than 120 s.
- JSON `ping` as the guaranteed app-level keepalive (already implemented).
- Opcode ping as defense in depth once verified with a packet capture or device log.

Do not disable JSON ping after enabling Uvicorn ping until firmware confirms opcode frames reset **its** 120 s timer.

### 2.6 Implementation sequence (server keepalive)

1. Accept `pong`; stop unknown_type.  
2. Add `ts_ms` to outbound ping (optional).  
3. Document in `backend_spec.md`.  
4. Confirm Caddy idle ≫ 120 s; music 4+ minutes.  
5. Metrics: pings sent, pongs received.

---

## 3. Wire contracts (server view)

Do not add `type: iot`. Do not advertise MQTT in OTA.

### 3.1 Already sent / received

| Direction | `type` | Server duty |
|-----------|--------|-------------|
| out | `hello` | `transport: websocket`, `session_id`, downlink `audio_params` 24 kHz / 60 ms |
| in | `hello` | Validate opus 16 kHz mono 60 ms, version 1 |
| in | `listen` / `abort` / `mcp` | Existing |
| out | `tts` / `stt` / `llm` / `mcp` | Existing |
| out | `alert` | TTS failure today; extend for battery/fall (P1) |
| out | `system` | Unused; reboot reserved |

### 3.2 Adopt officially

| Direction | Envelope | Server duty |
|-----------|----------|-------------|
| out | `ping` | Every 30 s while WS open, including SPEAKING |
| in | `pong` | Known type; metrics |
| in | `mcp` notification `notifications/phoe_lone.event` | Parse; **no JSON-RPC reply** |
| out | `mcp` `tools/call` `self.phoe_lone.*` | Only if discovered or fallback catalog; LLM pull |
| out | `llm` emotion | Turn-scoped; do not spam idle fidget (idle is on-device) |
| out | `alert` | Low battery / fall / sensor fault (P1) |

### 3.3 MCP pull JSON firmware will return (after PC work)

**IMU**

```json
{
  "wired": true,
  "sensor": "MPU6050",
  "ax": 0.02, "ay": 0.01, "az": 1.00,
  "gx": 0.1, "gy": -0.2, "gz": 0.0,
  "pitch": 2.4, "roll": -1.1,
  "temp_c": 31.2,
  "event": "still"
}
```

`event`: `still` | `moving` | `pickup` | `putdown` | `fall` | `shake`.  
Fail: `{"wired":true,"ok":false,"error":"i2c_nack"}`.

**Light:** `{ "wired": true, "lux": 120, "bucket": "indoor", "raw": 1840 }`  
`bucket`: `dark` | `dim` | `indoor` | `bright`.

**Touch:** `{ "wired": true, "touched": true, "count": 14, "ms_held": 320 }`

Old firmware: `wired: false`. Prompts must handle **both** until all robots are flashed.

### 3.4 Notification envelope (device → you)

```json
{
  "session_id": "<from hello>",
  "type": "mcp",
  "payload": {
    "jsonrpc": "2.0",
    "method": "notifications/phoe_lone.event",
    "params": {
      "event": "pet",
      "ts_ms": 1710000000000,
      "imu": { "pitch": 8.0, "az": 0.2 },
      "light": { "bucket": "indoor" }
    }
  }
}
```

`event`: `pickup` | `putdown` | `fall` | `pet` | `bright` | `dark`.

- **No `id`.** Do not `send_request` a reply.
- Firmware coalesces (~2/s) and suppresses pet during speaking. Still debounce on the server (duplicate pets).
- `fall`: firmware already stopped servos. You may `alert` + skip motion tools until IMU `event` is `still`.

### 3.5 Sensor vs turn policy (server column)

| Situation | Server |
|-----------|--------|
| WS closed | Nothing (no socket) |
| Listening, `pet` / `pickup` | Optional Gemini INTERNAL EVENT; **do not** start a new listen or steal the mic |
| SPEAKING / music, `pet` | Ignore (firmware should not send) |
| `fall` | `alert`; gate Otto motion tools; do not TTS a novel over music without abort |
| `dark` / `bright` | Log; optional mood. Do not force `llm` emotion every lux tick |
| Idle fidget | **Not your job.** Do not send periodic `llm` to “keep it alive” |

---

## 4. MCP sensor notifications

### 4.1 Bug to fix

`app/mcp/client.py`:

```python
def on_message(self, payload: dict[str, Any]) -> None:
    method = payload.get("method")
    if isinstance(method, str) and method.startswith("notifications"):
        handler(payload)  # DeviceSession._on_sensor_event
        return
```

JSON-RPC notifications have **no `id`**, so they also fail the later `id` int check. The early return is the explicit drop.

### 4.2 Target behavior

```
McpClient.on_message
  if method startswith notifications:
      callback or queue → DeviceSession._on_sensor_event(params)
      return
  if id in pending: resolve future  # existing tools/call replies
```

Do not use `asyncio.Lock` in a way that deadlocks `send_request` (non-reentrant; already documented).

Register a callback at session start: `mcp.set_notification_handler(self._on_mcp_notification)`.

### 4.3 `_on_sensor_event` (session.py)

1. Validate `event` enum; ignore unknown.
2. Log structured: `session.phoe_lone_event`, device_id, event.
3. Rate-limit per session (e.g. 2/s same event).
4. If `event == "fall"`: set `self._motion_inhibited_until = now + 5s`; `ToolRouter` Otto motion tools return skipped (except `self.otto.stop`). Optional `alert(..., "sad")`.
5. If state is SPEAKING: return after logging unless fall.
6. If LISTENING or READY after tts/stop auto-listen: optionally §5 inject. v1 may **log only** for pet; injection is P1.8.
7. Never `mcp.call` from the notification path in a way that blocks the receive loop — spawn a task.

### 4.4 Pull tools

Gemini may still call `self.phoe_lone.imu.get_reading` during a voice turn. `tool_router.dispatch` already forwards `self.*`. After firmware is live, **enrich catalog descriptions** (§6) so the model knows sensors work. Parse `wired` in the tool result if you want to strip fake numbers from the prompt context — `as_function_response` should pass through JSON; the sanitizer already blocks speaking JSON.

Do **not** poll sensors on a server timer. Pull is LLM-driven; push is notify.

---

## 5. Gemini Live injection

### 5.1 Existing pattern

Music end already sends Gemini `send_client_content` INTERNAL EVENT (`README.md`). Reuse that path — do not open a new Live socket.

### 5.2 When to inject (P1.8)

- Session has `brain` connected.
- Device state is **not** SPEAKING (no barge-in TTS from a pet mid-sentence).
- User is in auto-listen (channel open) **or** you accept a one-line TTS only after current tts/stop.
- Event is `pet` or `pickup` (not every `bright` tick).

Payload sketch (server → Gemini, not the ESP32):

```
INTERNAL EVENT: the user just petted the robot's head.
Do not call tools. One short Burmese sentence or empty string.
```

Same rules as music-end: empty string allowed; no JSON speech; `max_tool_rounds` not consumed by this.

### 5.3 When not to inject

- `fall` → local safety + `alert`; maybe one sentence **after** audio is idle.
- SPEAKING / music.
- `wired:false` era (no events).
- Rapid pets: one inject per 10 s.

### 5.4 Do not

- `listen/start` from the server.
- Fake STT text as if the user spoke.
- Call `self.otto.action` automatically on pet (firmware already did a local GIF). Duplicate motion fights the idle director.

---

## 6. Catalog, prompts, tools

### 6.1 Files

- `app/mcp/catalog.py` — `self.phoe_lone.imu.get_reading`, `.light.get_level`, `.touch.get_state` dual-fleet (`wired:true` / `wired:false`).
- `app/ai/prompts.py` — HARDWARE: sensors may be wired; still handle stubs; fall → stop.
- `PHOE_LONE_FALLBACK_NAMES` already includes the three tools.

### 6.2 After firmware reports `wired: true`

Update descriptions:

- IMU: real MPU6050; use for “did you fall”, “are you being held”; never invent ax if tool errors.
- Light: buckets; “is it dark” → call tool.
- Touch: “I petted you” may arrive as notify **or** user speech; do not require the user to say it.

Keep: “If `wired:false` or `ok:false`, say the sensor is not connected / failed.” Dual-fleet safe.

Fall: “If IMU event is fall or the user says you fell, call `self.otto.stop` if moving; do not walk.” Server-side otto_gate already suppresses stop on silence — do not weaken that.

### 6.3 Hands

Prompt already: no hand servos. Keep. Firmware will make `has_hands_` false; hand actions error. Catalog Otto description already says hand actions fail on 4-servo build.

### 6.4 Emotion vs idle

`set_emotion` remains a host tool. Idle GIF cycling is **firmware**. Do not send `llm` every 10 s to simulate breathing.

P1.2 (firmware maps emotion → short motion): optional prompt line: “Call set_emotion; the body may gesture by itself.” Do not double-call `self.otto.action` for every smile unless the user asked to dance.

---

## 7. Server OTA endpoint

### 7.1 Today (`app/api/ota.py`)

- Requires `Device-Id` + `Client-Id`.
- Optional POST body (system info); location extract from `board.ssid` / BSSID.
- `prepare_ota` returns a **stable** bearer after provision (wrapped `token_ciphertext`). Rotate only via CLI `phoe-lone rotate` or first wrap-migrate of hash-only rows.
- Response: `websocket.url/token/version`, `server_time`, `firmware.version` + `url` + `force: 0`.
- No `mqtt`, no `activation` (good for private VPS).
- Dummy URL: `settings.resolved_firmware_url` → `{origin}/firmware/none.bin` unless `FIRMWARE_URL` set.
- `firmware_version` default `0.0.0`.
- Rate limit `ota_rate_limit_per_minute`.

Device skips upgrade when version is not newer. 404 on the bin is extra safety.

### 7.2 P0.5 — token rotation

`prepare_ota` does **not** rotate on every version check.

Implemented: persist wrapped token at provision (`AUTH_PEPPER` XOR+HMAC wrap in `app/auth/token_wrap.py`). OTA JSON `websocket.token` echoes the same string the device already has in NVS. CLI `phoe-lone rotate` remains the explicit rotate path. Hash-only legacy rows (`token_ciphertext` null) rotate once on the next active OTA (`auth.token_wrap_migrate`).

### 7.3 Dummy firmware (keep in lab)

- `FIRMWARE_VERSION=0.0.0`
- `force: 0`
- `/firmware/none.bin` → 404
- Never `force: 1` in production defaults

### 7.4 Production OTA (P2.3) — server side

1. Read POST `board.type`, `application.version`, `application.elf_sha256`.
2. Channel map: `phoe-lone` → object storage / volume path. Ignore other Otto boards or serve empty upgrade.
3. If candidate semver **>** device version: HTTPS URL, `Content-Type` octet-stream, `Content-Length`, immutable object.
4. Optional: signed URL TTL 15–30 min.
5. HTTPS only (`PUBLIC_HTTP_ORIGIN`).
6. Same OTA response still includes **stable** websocket token + `server_time` (`timezone_offset` 390).
7. Staging vs prod buckets. Metrics: `OTA_REQUESTS` already; add `firmware_offered=true/false`.
8. Do not couple token rotate to firmware offer.

Serving the `.bin`: FastAPI `FileResponse` behind Caddy, or Caddy `file_server` for `/firmware/` only. Checksum in JSON is optional; ESP-IDF OTA has its own image checks.

### 7.5 Activation object

Omit `activation` for this VPS (device would show a code UI). Do not add it without a console.

---

## 8. Session / WS stability

### 8.1 Existing knobs (`app/config.py`)

| Setting | Default | Role |
|---------|---------|------|
| `hello_timeout_s` | 8 | Device hello |
| `keepalive_interval_s` | 30 | JSON ping |
| `device_idle_timeout_s` | 100 | Server close if no inbound (not during TTS/music) |
| `listen_idle_timeout_s` | 8 | VAD hangover |
| `max_concurrent_sessions` | 32 | |
| `ota_websocket_version` | 1 | Raw Opus |

Device 120 s is **firmware**. Server 100 s inbound idle is slightly tighter than 120 s outbound-only silence — uplink Opus during listen keeps `_last_rx` fresh. During thinking before first TTS, inbound may be quiet: 100 s is enough. During music, SPEAKING skip applies.

### 8.2 Reconnect

`SessionManager.attach`: new WS for same `device_id` closes the previous session. Gemini resumption handle is on `GeminiLiveBrain` **inside** that session → **lost**. P2.2 memory is Postgres, not this handle.

Do not try to migrate a Live socket across processes in P0.

### 8.3 Caddy

Idle timeouts must exceed 120 s + music duration. WebSocket `flush_interval` / `idle_timeout` in Caddy: set explicitly if default cuts long TTS. Firewall 80/443 only.

### 8.4 Writer queue

`outbound_queue_size` 200. Ping + Opus frames. Generation filter must not drop `tts/stop` after abort (already uses current generation in `_abort`). Do not put ping on generation `-1` unless you also change the writer — keep current generation.

---

## 9. P0 / P1 / P2 checklists (Python codebase)

Firmware boxes are in `CLIENT_PRODUCTION_PLAN.md`. Do not mix.

### P0 — keepalive, notify plumbing, auth, lab OTA

- [x] **P0.5** OTA does not rotate WS token on every check; live session survives a duplicate OTA POST.
- [x] **P0.6** Inbound `pong` is a first-class type; no `unknown_type`.
- [ ] **P0.9** Lab remains `0.0.0` + 404 bin; `force: 0`; documented in README.
- [x] **P0.S5** `notifications/*` not dropped; `notifications/phoe_lone.event` logged with device_id.
- [x] **P0.S6** Prompts/catalog: sensors **may** be wired; still handle `wired:false`; never invent ax/lux; fall → prefer stop not walk.
- [ ] Ping continues during SPEAKING/music (verify loop; add a test if missing).
- [x] `backend_spec.md`: `ping` / `pong` / notification schema.
- [x] Contract test: skip ping, accept pong; notify does not throw.
- [x] Dual-fleet: stub `wired:false` still lists tools and does not 500.

### P1 — feel + ops signals

- [ ] **P1.2** Prompt: emotion tool is enough; do not spam Otto actions for every smile (firmware gestures).
- [ ] **P1.6** Music abort already exists; do not regress `_abort` / generation bump.
- [ ] **P1.7** Optional: poll or cache `self.battery.get_level` rarely; or send `alert` when notify/status says low (do not block TTS path).
- [ ] **P1.8** Gemini INTERNAL EVENT on `pet`/`pickup` while listening, rate-limited; empty reply OK.
- [ ] Fall: motion inhibit window is in `_on_sensor_event` (5 s, Otto motion skipped except `self.otto.stop`); optional `alert` still open.
- [ ] Metrics: `phoe_lone_events_total{event=}`, `ws_pongs_total`.

### P2 — product

- [ ] **P2.1** Optional `server_time` already sent; morning greeting is prompt + local clock — add prompt only if firmware sleep exists.
- [ ] **P2.2** Postgres memory (name, likes) injected into Live `system_instruction` or a prefix turn.
- [ ] **P2.3** Board channel `phoe-lone`; HTTPS artifact URL; no dummy when `FIRMWARE_VERSION` set; checksum/logging.
- [ ] **P2.4** If firmware enables AEC: accept hello `features.aec`, protocol v2 timestamps — **only then** change `ota_websocket_version` / binary framing. Default stay v1 until both sides ship together.
- [ ] **P2.5** User-only MCP listing remains `withUserTools: false` for Gemini; companion API later.
- [ ] **P2.6** Optional crash ingest endpoint (auth); do not build until firmware posts dumps.
- [ ] **P2.7** Glyph-push on `stt`/`sentence_start` if firmware advertises `features.glyph_push`.
- [ ] **P2.10** Per-device Gemini/TTS rate limits beyond current WS limiter.

Out of scope: MQTT voice, 4G, LivingAI APIs, C++.

### Acceptance (server-only)

- [x] `pong` not in unknown_type logs.
- [x] Sending notify JSON in a unit test hits `_on_sensor_event`, not drop.
- [ ] 4-minute music: session stays open (ping still queued).
- [x] Second OTA POST does not invalidate the current WS bearer.
- [x] Prompt does not tell the model sensors are unwired **after** catalog switch; still safe if one device is old.
- [ ] Dummy firmware still 404 in lab Compose.

---

## 10. File map

| Path | Change |
|------|--------|
| `app/protocol/messages.py` | `ping` + optional `ts_ms`; inbound not needed here |
| `app/protocol/models.py` | `PongMessage`; optional notify params model |
| `app/sessions/session.py` | `_on_json` pong; `_on_sensor_event`; ping during speak (verify) |
| `app/mcp/client.py` | Notification callback instead of drop |
| `app/mcp/catalog.py` | Wired sensor copy |
| `app/ai/prompts.py` | Hardware + notify policy |
| `app/ai/gemini.py` | Reuse INTERNAL EVENT helper for pet |
| `app/ai/tool_router.py` | Fall motion inhibit if session flag |
| `app/auth/service.py` | Stable token on OTA (done) |
| `app/auth/token_wrap.py` | HMAC wrap for `token_ciphertext` (done) |
| `alembic/versions/0003_token_ciphertext.py` | Nullable wrapped-token column (done) |
| `app/api/ota.py` | Token + later firmware channel |
| `app/api/health.py` | Dummy bin stays 404 in lab |
| `app/config.py` | Optional `firmware_*` already; ping interval |
| `backend_spec.md` | ping/pong/notify (done) |
| `tests/contract/test_websocket.py` | pong; notify (done) |
| `tests/unit/test_catalog.py` | descriptions (done) |
| `README.md` | Lab OTA dummy; token rotate policy |

### First backend slices (order)

1. `pong` + notification callback + spec/tests (works with old firmware).  
2. Stable OTA token.  
3. Catalog/prompt dual wired/unwired.  
4. Fall inhibit + alert.  
5. Gemini pet INTERNAL EVENT.  
6. Real firmware URL behind flags (P2).

---

## 11. Decision log (server)

| Decision | Choice |
|----------|--------|
| Keepalive | Keep JSON `ping`; add `pong` handler; opcode ping is extra |
| Sensors | Do not poll; notify + LLM pull |
| Idle EMO | Firmware-only; no periodic `llm` |
| OTA lab | `0.0.0` + 404 |
| Token | Stable across version checks |
| AEC/v2 | Coupled release with firmware; default v1 |
| MQTT | Still omitted from OTA |
