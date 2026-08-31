# Phoe Lone Backend Specification

This document is the XiaoZhi **wire protocol** (OTA, WebSocket, MCP, audio framing) derived from the ESP-IDF Mickey client ([thukhasusan78/phoelone](https://github.com/thukhasusan78/phoelone)). How **this** FastAPI repo actually runs (Silero VAD, Gemini Live, local music) is in [README.md](README.md). Do **not** invent extra device-side APIs.

**Live board identity:** `mickey` (`POST` body `board.type`, compile-time `BOARD_TYPE`). Legacy `otto-robot` and `phoe-lone` are still accepted by this backend.

**Primary transport:** WebSocket. MQTT + UDP is optional and must still be implemented if the OTA JSON advertises it.

**Firmware profile:** `python scripts/build.py mickey --name mickey --language en-US`  
**Chip:** ESP32-S3, 16 MB flash, 8 MB octal PSRAM  
**OTA URL (baked in `main/boards/mickey/config.json` `CONFIG_OTA_URL`):**

```
https://phoelone.thukha.online/xiaozhi/ota/
```

---

## 1. System architecture

```
ESP32 (mickey firmware)
  │  boot
  ├─ HTTP POST  /xiaozhi/ota/           →  websocket NVS, optional firmware URL, activation code if unbound
  ├─ HTTP POST  /xiaozhi/ota/activate   →  202 until portal bind, then 200
  ├─ HTTPS GET  /                       →  6-digit activation portal
  ├─ WebSocket  wss://phoelone.thukha.online/xiaozhi/v1/   →  hello, Opus, JSON, MCP
  └─ optional MQTT + UDP          →  same JSON as WS; audio on UDP/AES-CTR

VPS backend (you implement)
  ├─ OTA HTTP service
  ├─ WebSocket voice session (ASR → LLM → TTS → MCP)
  ├─ Server-side tools (weather, news, music, knowledge, email, …)
  └─ Device-side MCP client (initialize, tools/list, tools/call)
```

The ESP32 is an **MCP server** (it exposes tools). The VPS is an **MCP client** (it discovers and calls those tools) **and** an LLM host that also has **its own** tools (weather/news/music). Device tools never include `search_weather`; those live only on the server.

---

## 2. Boot and OTA HTTP

### 2.1 When it runs

After Wi-Fi connects, `Ota::CheckVersion()` (`main/ota.cc`) POSTs (or GETs if body empty) to the OTA URL. A 200 JSON body is required. Non-200 fails activation.

### 2.2 Request

- **Methods:** `POST` (normal) or `GET` if the device has no system-info body.
- **URL:** `CONFIG_OTA_URL` or NVS `wifi` / `ota_url`.
- **Also accept** the same path without trailing slash.

**Headers (always):**

| Header | Value |
|--------|--------|
| `Activation-Version` | `"1"` or `"2"` (2 if eFuse serial exists) |
| `Device-Id` | Wi-Fi MAC, e.g. `aa:bb:cc:dd:ee:ff` |
| `Client-Id` | UUID string (NVS; changes if NVS erased) |
| `Serial-Number` | only if Activation-Version is 2 |
| `User-Agent` | ESP-IDF user-agent string |
| `Accept-Language` | firmware language code, e.g. `en-US` |
| `Content-Type` | `application/json` |

**POST body** (`Board::GetSystemInfoJson()`), schema (version 2):

```json
{
  "version": 2,
  "language": "en-US",
  "flash_size": 16777216,
  "minimum_free_heap_size": "123456",
  "mac_address": "aa:bb:cc:dd:ee:ff",
  "uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "chip_model_name": "esp32s3",
  "chip_info": { "model": 9, "cores": 2, "revision": 0, "features": 0 },
  "application": {
    "name": "xiaozhi",
    "version": "2.4.2",
    "compile_time": "Aug 18 2026T12:00:00Z",
    "idf_version": "v6.0.2",
    "elf_sha256": "<64 hex chars>"
  },
  "partition_table": [
    { "label": "nvs", "type": 1, "subtype": 2, "address": 0, "size": 0 }
  ],
  "ota": { "label": "ota_0" },
  "display": { "monochrome": false, "width": 240, "height": 240 },
  "board": {
    "type": "mickey",
    "name": "mickey",
    "manufacturer": "<BOARD_MANUFACTURER>",
    "ssid": "HomeWifi",
    "rssi": -45,
    "channel": 6,
    "ip": "192.168.1.50",
    "mac": "aa:bb:cc:dd:ee:ff"
  }
}
```

`board.ssid` / `rssi` / `channel` / `ip` are omitted while the device is in Wi-Fi config mode.

### 2.3 Response (HTTP 200 JSON)

The device parses these **top-level objects**. Unknown keys are ignored. String/number fields inside `websocket` and `mqtt` are copied **verbatim** into NVS namespaces of the same name.

```json
{
  "websocket": {
    "url": "wss://phoelone.thukha.online/xiaozhi/v1/",
    "token": "<opaque-token>",
    "version": 1
  },
  "mqtt": {
    "endpoint": "mqtt.example.com:8883",
    "client_id": "device-mac",
    "username": "user",
    "password": "pass",
    "keepalive": 240,
    "publish_topic": "device/out"
  },
  "server_time": {
    "timestamp": 1710000000000,
    "timezone_offset": 390
  },
  "firmware": {
    "version": "0.0.0",
    "url": "https://phoelone.thukha.online/firmware/none.bin",
    "force": 0
  },
  "activation": {
    "message": "Please enter the verification code in phoelone.thukha.online",
    "code": "123456",
    "challenge": "<uuid>",
    "timeout_ms": 30000
  }
}
```

**Field rules:**

| Object | Required for Phoe Lone WS | Behavior |
|--------|---------------------------|----------|
| `websocket.url` | **Yes** | Stored as NVS `websocket`/`url`. Must be `ws://` or `wss://`. Path typically `/xiaozhi/v1/` or `/xiaozhi/v1`. |
| `websocket.token` | Recommended | NVS `websocket`/`token`. Device sends `Authorization: Bearer <token>` unless the token already contains a space. |
| `websocket.version` | Optional number | NVS `websocket`/`version`. `1` (default raw Opus), `2`, or `3`. |
| `mqtt.*` | Only if using MQTT transport | Every string/number child is stored under NVS `mqtt`. If omitted, device logs “No mqtt section”. |
| `server_time.timestamp` | Recommended | Milliseconds. Device adds `timezone_offset` minutes then `settimeofday`. Myanmar offset example: `390`. |
| `firmware.version` + `firmware.url` | Recommended | If `version` is **newer** than running firmware, device starts OTA download. Use a dummy version `0.0.0` and a non-downloadable URL to skip upgrades. `force: 1` forces install even if not newer. |
| `activation` | Pending devices only | If `code` is present, the robot shows the activation UI and plays digit sounds, then polls `POST /xiaozhi/ota/activate`. **Omit this object once the device is bound**, or the firmware stays on the activation screen. DIY Otto boards have no eFuse HMAC: `Activation-Version: 1` and activate body `{}`. |

### 2.4 Device activation poll and web portal

**Pending (unbound) `Device-Id` + `Client-Id`:** OTA HTTP 200 includes `activation` (6-digit `code`, UUID `challenge`, portal `message`) plus `websocket.url` / `token`. WebSocket authenticate is **403** until bound.

**Activate poll:** `POST /xiaozhi/ota/activate` (and `/xiaozhi/ota/activate/`). Same `Device-Id` / `Client-Id` headers. Body may be `{}` (v1) or an HMAC payload (v2, ignored except optional `challenge` match).

| Status | Meaning |
|--------|---------|
| 202 | Still waiting for the user to enter the code at `https://phoelone.thukha.online/` |
| 200 | Bound. Firmware re-runs CheckVersion; that OTA JSON **must not** include `activation`. |
| 403 | Device disabled |
| 400 | Identity missing, or `challenge` does not match |

**Web portal:** `GET /` (HTML form). `POST /activate` with form field `code` or JSON `{"code":"123456"}`. A valid pending code marks the device `active` and sets an HttpOnly `companion` cookie (`device_id` + `client_id`, HMAC with `AUTH_PEPPER`, default 30 days). Codes expire after `ACTIVATION_TTL_S` (default 15 minutes); the next OTA issues a new code.

`GET /` with a valid companion cookie renders the dashboard (presence, dance pad, Rock-Paper-Scissors). Without a cookie it stays the activation form. `POST /companion/logout` clears the cookie. If `COMPANION_PIN` is set, `POST /companion/unlock` (`pin` form/JSON) binds the same cookie to the active device so a second browser can open the dashboard.

**Companion WebSocket:** `wss://…/companion/v1/` (cookie auth, close `1008` if missing). JSON frames only — this is **not** the XiaoZhi device protocol. See §12.

**Bound / CLI-provisioned:** OTA omits `activation`. `ALLOW_AUTO_PROVISION=false` rejects unknown devices with 403 (no code).

**Live Mickey firmware** bakes `CONFIG_OTA_URL` into `main/boards/mickey/config.json`. OTA `board.type` is `mickey`. This backend also accepts legacy `otto-robot` and `phoe-lone`.

---

## 3. WebSocket voice session (required)

### 3.1 Connection

- **URL:** NVS `websocket.url` from OTA.
- **Subprotocol:** none required.
- **Timeouts:** server hello must arrive within **10 s**. Idle timeout on device is **120 s** since last incoming frame.

Handshake headers from device:

```
Authorization: Bearer <token>
Protocol-Version: 1
Device-Id: <mac>
Client-Id: <uuid>
```

### 3.2 Device → server `hello` (first text frame)

Built by `WebsocketProtocol::GetHelloMessage()`:

```json
{
  "type": "hello",
  "version": 1,
  "features": {
    "mcp": true
  },
  "transport": "websocket",
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

If `CONFIG_USE_SERVER_AEC` is enabled, `features.aec` is `true`. If assets advertise glyph push, `features.glyph_push` is `true` and `text_font` is present (`bundle`, `charset`, `size`, `bpp`).

`version` matches NVS `websocket.version` from OTA (this backend sends `1`). `frame_duration` is `OPUS_FRAME_DURATION_MS` = **60**.

This server speaks **protocol v1 raw Opus only**. If `hello.version` is not `1` or `features.aec` is `true`, the handshake is rejected (WebSocket close **1003**, reason logged as `session.hello_rejected`). Do not mix v1 raw frames with v2 timestamped binary until both sides ship AEC together.

### 3.3 Server → device `hello` (required)

Must include `"type":"hello"` and `"transport":"websocket"`. Any other `transport` is rejected and the channel never opens.

```json
{
  "type": "hello",
  "transport": "websocket",
  "session_id": "a-uuid-you-generate",
  "audio_params": {
    "format": "opus",
    "sample_rate": 24000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

The device stores `session_id` and uses `audio_params.sample_rate` / `frame_duration` for **downlink** Opus decode (typically 24000 Hz). Uplink stays 16000 Hz.

### 3.4 Binary audio

| Direction | Content |
|-----------|---------|
| Device → server | Opus frames of microphone audio after local processing. **v1:** raw Opus bytes as WS binary. **v2:** packed `BinaryProtocol2` (all multi-byte fields **network byte order**). **v3:** `BinaryProtocol3`. |
| Server → device | Opus frames to play. Device **drops** downlink audio while in `listening` state. Send audio only after `tts/start` (device enters `speaking`). |

`BinaryProtocol2` (`__attribute__((packed))`):

```
uint16 version; uint16 type;  // type 0 = OPUS, 1 = JSON
uint32 reserved;
uint32 timestamp_ms;          // useful if features.aec
uint32 payload_size;
uint8  payload[];
```

`BinaryProtocol3`:

```
uint8  type;
uint8  reserved;
uint16 payload_size;          // network byte order
uint8  payload[];
```

Uplink Opus: 16 kHz, mono, 60 ms frames. Downlink: match `audio_params` in server hello (24 kHz recommended).

### 3.5 Device → server JSON

Every message except the first hello includes `session_id`.

#### listen / start

```json
{ "session_id": "xxx", "type": "listen", "state": "start", "mode": "auto" }
```

`mode`:

- `"auto"` — `kListeningModeAutoStop` (typical after wake word / button in auto mode)
- `"manual"` — press-to-talk / click-to-talk until stop
- `"realtime"` — full duplex / server AEC mode

After `start`, the device streams binary Opus until `listen/stop`, abort, or `tts/start`.

**This backend:** in `auto` / wake-word mode the ESP32 often never sends `listen/stop`. The server runs Silero VAD, ends the Gemini turn on silence (or `MAX_FORWARDED_AUDIO_SECONDS`), and sends `tts/start` so the device leaves listening.

#### listen / stop

```json
{ "session_id": "xxx", "type": "listen", "state": "stop" }
```

#### listen / detect (wake word)

```json
{ "session_id": "xxx", "type": "listen", "state": "detect", "text": "Hi XiaoZhi" }
```

`text` is the wake-word phrase. Opus of the wake snippet may already have been sent.

#### abort

```json
{ "session_id": "xxx", "type": "abort", "reason": "wake_word_detected" }
```

`reason` is omitted unless abort is due to a new wake word (`kAbortReasonWakeWordDetected`). Stop TTS immediately.

#### pong

Optional reply to server JSON `ping`. Old firmware may omit this; the session stays valid either way.

```json
{ "session_id": "xxx", "type": "pong", "ts_ms": 1710000000000 }
```

`ts_ms` may echo the ping timestamp. The server treats `pong` as a known type (it is not listen/abort/MCP) and does not require it.

#### mcp

```json
{ "session_id": "xxx", "type": "mcp", "payload": { "jsonrpc": "2.0", "id": 1, "result": {} } }
```

`payload` is a JSON-RPC **response** (or a notification). See §5.

WebSocket does **not** send `goodbye`. Closing the socket is enough.

### 3.6 Server → device JSON (`Application::OnIncomingJson`)

Unknown `type` is logged and ignored. Missing `type` is logged as an error.

#### tts

```json
{ "session_id": "xxx", "type": "tts", "state": "start" }
```

Device → `kDeviceStateSpeaking`, starts playing binary Opus.

```json
{ "session_id": "xxx", "type": "tts", "state": "sentence_start", "text": "Hello, I am Phoe Lone." }
```

Shows assistant subtitle. Optional glyph-push fields: see `docs/glyph-push.md`.

```json
{ "session_id": "xxx", "type": "tts", "state": "stop" }
```

If listening mode is manual → Idle; else → Listening again (auto continue).

**Minimum TTS cycle for a spoken reply:** `start` → one or more `sentence_start` + binary Opus → `stop`.

#### stt

```json
{ "session_id": "xxx", "type": "stt", "text": "walk forward two steps" }
```

Shows user bubble. Send after ASR of the current utterance.

#### llm (face / emotion)

```json
{ "session_id": "xxx", "type": "llm", "emotion": "happy", "text": "😀" }
```

`emotion` is passed to `Display::SetEmotion`. Otto GIF assets use names such as `staticstate`, `neutral`, `happy`, `sad`, `sleepy`, plus other otto-gif keys. If unknown, the display logs and may keep the last face. `text` is optional for servers; the official cloud sometimes sends an emoji.

#### mcp

```json
{
  "session_id": "xxx",
  "type": "mcp",
  "payload": {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 2,
    "params": { "name": "self.otto.action", "arguments": { "action": "walk", "steps": 1, "speed": 2000, "direction": 1 } }
  }
}
```

#### ping

Application keepalive every ~30 s while the WebSocket is open, including during TTS/music. Independent of WebSocket opcode ping.

```json
{ "session_id": "xxx", "type": "ping", "ts_ms": 1710000000000 }
```

#### system

```json
{ "session_id": "xxx", "type": "system", "command": "reboot" }
```

Only `reboot` is implemented. Device schedules `Application::Reboot()`.

#### alert

All three strings required:

```json
{
  "session_id": "xxx",
  "type": "alert",
  "status": "Warning",
  "message": "Battery low",
  "emotion": "sad"
}
```

Plays vibration OGG and shows status/message/emotion.

#### custom (only if `CONFIG_RECEIVE_CUSTOM_MESSAGE`)

```json
{ "session_id": "xxx", "type": "custom", "payload": { "message": "anything" } }
```

Shows `payload` JSON as a system chat line. Otto default builds may not enable this.

### 3.7 Recommended session sequence (auto mode)

1. Device connects, sends `hello`.
2. Server replies `hello` with `session_id`.
3. Server immediately sends MCP `initialize` then `tools/list` (may be after first listen; doing it at hello is better so tools exist before the LLM turn).
4. Device sends `listen/start` + Opus.
5. Server: ASR → `stt` → LLM (optionally MCP `tools/call`) → `llm` emotion → `tts/start` → Opus + `sentence_start` → `tts/stop`.
6. Device returns to listening (auto) or idle (manual).
7. Socket close → device Idle.

---

## 4. MQTT + UDP (optional)

Implement if OTA includes a `mqtt` object. JSON **control** messages are identical to §3.5–3.6 except:

- Device hello uses `"transport": "udp"` and typically `"version": 3`.
- Server hello **must** include:

```json
{
  "type": "hello",
  "transport": "udp",
  "session_id": "xxx",
  "audio_params": { "format": "opus", "sample_rate": 24000, "channels": 1, "frame_duration": 60 },
  "udp": {
    "server": "203.0.113.10",
    "port": 8888,
    "key": "0123456789ABCDEF0123456789ABCDEF",
    "nonce": "0123456789ABCDEF0123456789ABCDEF"
  }
}
```

`key` and `nonce` are hex-encoded 128-bit AES-CTR material.

- MQTT settings from OTA: `endpoint` (`host` or `host:port`, default port **8883**), `client_id`, `username`, `password`, `keepalive` (default 240), `publish_topic` (device publishes hello/listen/mcp here). Incoming MQTT payloads are parsed as JSON (subscribe topic is server-defined; typically a per-device topic).
- Extra types: device may send `{ "type": "goodbye", "session_id": "xxx" }`. Server `goodbye` with matching `session_id` closes UDP **without** the device echoing goodbye.
- UDP packet (AES-CTR on Opus payload): `type=0x01`, `flags`, `payload_len` BE, `ssrc`, `timestamp` BE, `sequence` BE, then ciphertext. Counter is derived from timestamp + sequence (see `mqtt_protocol.cc` and `docs/mqtt-udp.md`). Drop replayed sequences.

Phoe Lone can ship WS-only: omit `mqtt` from OTA JSON.

---

## 5. MCP (device tools)

### 5.1 Envelope

Always wrap JSON-RPC in:

```json
{ "session_id": "<from hello>", "type": "mcp", "payload": { } }
```

`payload.jsonrpc` **must** be `"2.0"`. Request/response `payload.id` **must** be a JSON **number** (not a string). JSON-RPC notifications have **no** `id`.

Device-initiated methods starting with `notifications/` are handled, not ignored. In particular `notifications/phoe_lone.event` is parsed and logged (`event`: `pickup` | `putdown` | `fall` | `pet` | `bright` | `dark`). The server **must not** send a JSON-RPC reply. Unknown notification methods are logged and dropped. Old firmware that never sends notifications remains valid.

```json
{
  "session_id": "xxx",
  "type": "mcp",
  "payload": {
    "jsonrpc": "2.0",
    "method": "notifications/phoe_lone.event",
    "params": {
      "event": "pet",
      "ts_ms": 1710000000000
    }
  }
}
```

Device replies are sent with `Protocol::SendMcpMessage` as the same envelope; `payload` is already a JSON-RPC object string.

### 5.2 initialize

**Server → device**

```json
{
  "jsonrpc": "2.0",
  "method": "initialize",
  "id": 1,
  "params": {
    "capabilities": {
      "vision": {
        "url": "http://<VPS>/vision/explain",
        "token": "optional-bearer"
      }
    }
  }
}
```

`vision.url` must be **HTTP(S)**, never WebSocket. Otto no-camera builds ignore it (no camera). If a camera exists, `self.camera.take_photo` POSTs the JPEG there.

**Device → server**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": { "tools": {} },
    "serverInfo": { "name": "mickey", "version": "2.4.2" }
  }
}
```

(`name` is compile-time `BOARD_NAME`.)

### 5.3 tools/list

```json
{
  "jsonrpc": "2.0",
  "method": "tools/list",
  "id": 2,
  "params": { "cursor": "", "withUserTools": false }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "self.get_device_status",
        "description": "...",
        "inputSchema": { "type": "object", "properties": {} }
      }
    ]
  }
}
```

If `nextCursor` is a non-empty string, call `tools/list` again with `cursor` equal to that name. Max payload ~8000 bytes.

`withUserTools: true` includes user-only tools (`annotations.audience: ["user"]`). The LLM should **not** receive those.

### 5.4 tools/call

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 3,
  "params": {
    "name": "self.audio_speaker.set_volume",
    "arguments": { "volume": 70 }
  }
}
```

Missing required args → JSON-RPC error `{ "message": "Missing valid argument: ..." }`. Unknown tool → `"Unknown tool: ..."`.

**Success result** (device wraps return values as text):

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{ "type": "text", "text": "true" }],
    "isError": false
  }
}
```

- `bool` → `"true"` / `"false"`
- `int` → decimal string
- `string` / `cJSON*` → that string (JSON objects are **stringified**, not nested)
- image content is rare (camera explain may return JSON text)

Tool callbacks run on the application task; do not block the WS loop on the server waiting more than a few seconds for motion tools.

### 5.5 Common tools (Mickey / XiaoZhi common set)

Registered in `McpServer::AddCommonTools` (`main/mcp_server.cc`). Visible to the LLM.

#### `self.get_device_status`

- Args: none
- Returns JSON string, example:

```json
{
  "audio_speaker": { "volume": 70 },
  "screen": { "brightness": 80, "theme": "dark" },
  "battery": { "level": 85, "charging": false },
  "network": { "type": "wifi", "ssid": "HomeWifi", "signal": "strong" },
  "chip": { "temperature": 45.2 }
}
```

`signal` is `strong` (rssi ≥ -60), `medium` (≥ -70), or `weak`. Battery/chip omitted if hardware reports none.

#### `self.audio_speaker.set_volume`

- `volume` (integer, **required**, 0–100)

#### `self.screen.set_brightness`

- `brightness` (integer, **required**, 0–100)
- Only registered if the board has a backlight (otto does: GPIO BLK 3).

#### `self.screen.set_theme`

- `theme` (string, **required**): `"light"` or `"dark"`

#### `self.camera.take_photo`

- Only if a camera object exists. Otto **no-camera** auto-detect does **not** register this.
- `question` (string, required): what to ask about the photo.
- Device captures, then HTTP-explains via the vision URL from `initialize`.

### 5.6 User-only tools (companion app)

Registered by `AddUserOnlyTools`. List only with `withUserTools: true`. Do not give these to the LLM.

| Name | Args | Effect |
|------|------|--------|
| `self.get_system_info` | none | Same JSON as OTA POST body |
| `self.reboot` | none | Reboots after ~1 s; returns `true` immediately |
| `self.upgrade_firmware` | `url` string | Downloads firmware from URL, installs, reboots |
| `self.screen.get_info` | none | `{ "width": 240, "height": 240, "monochrome": false }` |
| `self.screen.snapshot` | `url`, `quality` 1–100 default 80 | JPEG multipart POST `file=screenshot.jpg` |
| `self.screen.preview_image` | `url` | HTTP GET image, show on LCD |
| `self.assets.set_download_url` | `url` | NVS `assets`/`download_url` |

Snapshot/preview require `CONFIG_LV_USE_SNAPSHOT`.

### 5.7 Press-to-talk (if the board constructs `PressToTalkMcpTool`)

#### `self.set_press_to_talk`

- `mode`: `"press_to_talk"` or `"click_to_talk"`
- Saved in NVS `vendor`/`press_to_talk`

### 5.8 Otto / Phoe Lone motion tools (`otto_controller.cc`)

These are LLM-visible. On the **no-camera** Otto profile, hands GPIOs are `NC`; hand actions return an error string.

#### `self.otto.action`

Unified locomotion / dance / pose tool.

| Property | Type | Default | Range | Meaning |
|----------|------|---------|-------|---------|
| `action` | string | `"sit"` | see list | Motion name |
| `steps` | int | 3 | 1–100 | Repeats |
| `speed` | int | 700 | 100–3000 | **Smaller = faster** (ms-style) |
| `direction` | int | 1 | -1, 0, 1 | 1=forward/left, -1=back/right, 0=both |
| `amount` | int | 30 | 0–170 | Amplitude |
| `arm_swing` | int | 50 | 0–170 | Arm swing for walk/turn |

**`action` values (always available on 4-servo no-camera Otto):**

| action | Extra args used | Notes |
|--------|-----------------|-------|
| `walk` | steps, speed, direction, arm_swing | Forward/back walk |
| `turn` | steps, speed, direction, arm_swing | Turn in place |
| `jump` | steps, speed | |
| `swing` | steps, speed, amount | |
| `moonwalk` | steps, speed, direction, amount | |
| `bend` | steps, speed, direction | |
| `shake_leg` | steps, speed, direction | |
| `updown` | steps, speed, amount | |
| `whirlwind_leg` | steps, speed, amount | |
| `sit` | (ignored) | Sit pose |
| `showcase` | | Demo sequence |
| `home` | | Return to stand / home |

**Hand actions (fail with `"错误：此动作需要手部舵机支持"` without hand servos):**  
`hands_up`, `hands_down`, `hand_wave`, `windmill`, `takeoff`, `fitness`, `greeting`, `shy`, `radio_calisthenics`, `magic_circle`.

Unknown action returns a Chinese error listing all names.

**Voice mapping for the LLM (examples):**

- “move forward” / “walk” → `action=walk`, `direction=1`, `steps=2`, `speed=2000` (slow first tests)
- “go back” → `walk`, `direction=-1`
- “turn left” → `turn`, `direction=1`
- “turn right” → `turn`, `direction=-1`
- “dance” / “swing” → `swing` or `showcase`
- “jump” → `jump`
- “sit down” → `sit`
- “stand” / “reset” / “home” → `home`
- “stop” → **do not** use this tool; call `self.otto.stop`

Return: `true` or an error string.

#### `self.otto.stop`

- Args: none
- Deletes the action task, clears the queue, homes servos. **Always expose this to the LLM for safety.**

#### `self.otto.servo_sequences`

AI-authored servo programming. Single argument `sequence` (string containing JSON).

Top-level JSON:

```json
{
  "a": [ { "s": { "ll": 90, "rl": 90 }, "v": 1000, "d": 0 } ],
  "d": 0
}
```

- `a`: array of action objects
- top-level `d`: delay ms after the sequence

Each action is either:

**Move mode:** `s` map of servo → 0–180 deg, `v` duration 100–3000 ms (default 1000), `d` post-delay ms.

Servo keys: `ll` left leg, `rl` right leg, `lf` left foot, `rf` right foot, `lh` left hand, `rh` right hand.

**Oscillator mode:** `osc` object with `a` amplitudes, `o` centers, `ph` phase degrees, `p` period ms, `c` cycle count.

Safety: when oscillating legs/feet, **one foot must stay at 90°**. After multiple sequences, call `self.otto.action` with `home` or `self.otto.stop`. Queue holds 10 items; send short sequences.

The `sequence` property is a **string** (JSON escaped inside the MCP arguments), e.g.:

```json
{
  "name": "self.otto.servo_sequences",
  "arguments": {
    "sequence": "{\"a\":[{\"s\":{\"ll\":90,\"rl\":90},\"v\":1000}]}"
  }
}
```

#### `self.otto.set_trim`

- `servo_type`: `left_leg` | `right_leg` | `left_foot` | `right_foot` | `left_hand` | `right_hand`
- `trim_value`: int -50..50, persisted in NVS `otto_trims`
- Triggers a small jump to preview. Hand types error if no hands.

#### `self.otto.get_trims`

Returns JSON string:

```json
{ "left_leg": 0, "right_leg": 0, "left_foot": 0, "right_foot": 0, "left_hand": 0, "right_hand": 0 }
```

#### `self.otto.get_status`

Returns plain text `"moving"` or `"idle"`.

#### `self.battery.get_level`

```json
{ "level": 85, "charging": false }
```

#### `self.otto.get_ip`

```json
{ "ip": "192.168.1.50", "connected": true }
```

Empty IP → `{ "ip": "", "connected": false }`.

#### Phoe Lone sensors (pull tools)

Firmware may return `wired: false` (stub) or `wired: true` (live MPU6050 / light / touch). The LLM must handle both and must never invent `ax` / lux / touch when `wired:false` or `ok:false`.

This backend **does not** inject these names into Gemini unless device `tools/list` returned them. Empty MCP discovery uses the core Otto/Mickey fallback catalog only.

| Name | Typical JSON |
|------|-------------|
| `self.phoe_lone.imu.get_reading` | Stub: `{ "wired": false, "sensor": "MPU6050", "reason": "..." }`. Live: `{ "wired": true, "sensor": "MPU6050", "ax", "ay", "az", "gx", "gy", "gz", "pitch", "roll", "temp_c", "event" }` where `event` is `still` \| `moving` \| `pickup` \| `putdown` \| `fall` \| `shake`. I2C fail: `{ "wired": true, "ok": false, "error": "i2c_nack" }`. |
| `self.phoe_lone.light.get_level` | Stub: `{ "wired": false, ... }`. Live: `{ "wired": true, "lux": 120, "bucket": "indoor", "raw": 1840 }` (`bucket`: `dark` \| `dim` \| `indoor` \| `bright`). |
| `self.phoe_lone.touch.get_state` | Stub: `{ "wired": false, ... }`. Live: `{ "wired": true, "touched": true, "count": 14, "ms_held": 320 }`. A pet may also arrive as `notifications/phoe_lone.event`. |

If IMU `event` is `fall`, prefer `self.otto.stop`; do not walk.

### 5.6 Mickey alarm / sleep (`main/boards/mickey/mickey_alarm.cc`)

| Name | Args | Behavior |
|------|------|----------|
| `self.mickey.alarm.set` | `hour` 0–23, `minute` 0–59, `repeat` bool, `sleep_now` bool | Store wake time. **Firmware default `repeat=true` (daily) if omitted** — always pass `repeat` explicitly. `sleep_now=true` enters deep sleep until that time. |
| `self.mickey.alarm.get` | none | Stored alarm JSON (`enabled`, `hour`, `minute`, `repeat`, …). |
| `self.mickey.alarm.cancel` | none | Clear stored alarm. |
| `self.mickey.sleep.now` | optional `hour`, `minute`, `seconds` 1–86400 | Empty call uses the **stored enabled** alarm and fails with `no wake time; set hour/minute or seconds` if none is set. `seconds` is a bench timer (no synced clock). |

---

## 6. Server-side LLM tools (NOT on the ESP32)

The official XiaoZhi cloud and compatible servers expose **host** tools. The device never registers `search_weather`. Implement these on the VPS and let the LLM call them **locally**, then speak the answer via TTS.

Minimum set so “nothing is left behind” versus a full XiaoZhi-style assistant:

| Server tool | Typical args | Behavior |
|-------------|--------------|----------|
| `search_weather` | `location` string, optional `date` | Query a weather API; summarize in the user’s language; TTS the forecast. Do **not** send this name to device MCP. |
| `search_news` | `query` or `topic`, optional `count` | News API / RSS; speak headlines. |
| `search_music` | `query`, optional `play` bool | This backend auto-scans `data/local_music/` (`Artist - Title.mp3`). Generic “play a song” picks a random **local** file (no iTunes/YouTube). Named artist/title scores local first. After a short TTS announce, stream the **full** track as 24 kHz Opus on the TTS WebSocket (FFmpeg). Do not hum or invent lyrics. |
| `search_web` / knowledge | `query` | Web or RAG search; cite briefly in speech. |
| `send_email` | `to`, `subject`, `body` | Optional; original cloud MCP extension. |
| smart-home / PC control | vendor-specific | Original cloud MCP; out of scope unless you add cloud MCP servers. |

**LLM policy for Phoe Lone:**

1. If the user asks about device state (volume, battery, Wi-Fi) → device `self.get_device_status`.
2. If the user asks to move/dance/stop → device `self.otto.*`.
3. If the user asks weather/news/music/facts → **server** tools, then TTS.
4. Never block audio: run device MCP in parallel with LLM, but stop motion before long TTS if servos would fight.

---

## 7. Audio / ASR / TTS requirements

| Item | Value |
|------|--------|
| Uplink | Opus, 16 kHz, mono, 60 ms |
| Downlink | Opus, typically 24 kHz, mono, 60 ms |
| STT | Decode uplink Opus; this backend endpoints with server Silero VAD (device may not send `listen/stop`) then Gemini Live transcription |
| TTS | After LLM text, send `tts/start`, binary Opus, `sentence_start` per sentence, `tts/stop` |
| Barge-in | On new `listen/detect` or abort, stop TTS |
| AEC | This server refuses `features.aec` and `hello.version != 1` (close 1003). Stay on v1 raw Opus until both sides ship v2 together. |

If you cannot run real ASR/TTS yet, still send a valid TTS cycle (even a short silence Opus + “I heard you”) so the device leaves `listening`. Never leave the device in listening forever without `tts` or closing the socket.

---

## 8. Emotions / display

After each assistant turn send `type: llm` with `emotion` before or during TTS. Useful names: `neutral`, `happy`, `sad`, `sleepy`, `staticstate`. Otto idle default after UI setup is `staticstate`.

---

## 9. Optional on-device WebSocket (LAN debug)

Otto starts `WebSocketControlServer` on **port 8080**, path `/ws`. This is **not** the cloud protocol. A LAN client may send either:

```json
{ "type": "mcp", "payload": { "jsonrpc": "2.0", "method": "tools/call", "id": 1, "params": { "name": "self.otto.stop", "arguments": {} } } }
```

or a bare JSON-RPC object. MCP replies are broadcast to those clients. Do not confuse this with `ws://vps/xiaozhi/v1/`.

---

## 10. Authentication and multi-device

- Treat `Device-Id` (MAC) + `Client-Id` (UUID) + `Authorization` as the device identity.
- Issue a per-device `websocket.token` in OTA.
- Reject WS connections with a bad token (close handshake).
- `session_id` is per audio channel, not per device lifetime.

---

## 11. Error handling

| Case | Device behavior | Server should |
|------|-----------------|---------------|
| OTA non-200 / invalid JSON | Activation fails / retry | Always 200 + valid JSON |
| WS connect fail | Alert “cannot connect” | Keep `/xiaozhi/v1/` up |
| No hello in 10 s | Timeout error | Reply hello immediately |
| Malformed JSON | Log, ignore | Send valid `type` |
| MCP unknown tool | error message in JSON-RPC | Only call listed tools |
| 120 s silence | Channel timeout | Send keep-alive JSON or audio |

---

## 12. Companion dashboard (browser)

Cloud UI at `https://phoelone.thukha.online/` after activation. The browser never receives the device WebSocket bearer.

**URL:** `wss://phoelone.thukha.online/companion/v1/` (and `/companion/v1`). Cookie `companion` required before `accept`; otherwise close `1008`. Rate limit: `COMPANION_RATE_LIMIT_PER_MINUTE` (default 30).

Do not send these frames to the ESP32. The hub translates them to existing XiaoZhi envelopes on `/xiaozhi/v1/` (`type: mcp`, `type: llm`, `type: tts`).

**Server → browser**

| `type` | Fields |
|--------|--------|
| `hello` | `device_id` |
| `presence` | `online`, `state`, `emotion`, `battery`, `charging`, optional `sleeping` / `rebooting`, optional `hint` |
| `game.state` | `game: rps`, `match_id`, optional `round_id`, `you`, `mickey`, `winner`, `score`, `best_of`, `wins_needed`, `phase` (`awaiting_throw` \| `countdown` \| `match_over`), optional `countdown_ms` / `committed` while `phase` is `countdown`, optional `match_winner` when the match is over, optional `timeout` if nobody threw in time (both throws stay hidden until the reveal frame) |
| `error` | `code` (`offline` \| `busy` \| `invalid` \| `rate_limited`), `message` |
| `chat.user` | `text` — echo of a typed companion line (capped at 400 chars) |
| `chat.reply` | `text`, `emotion` — after Mickey speaks the answer |
| `memory.state` | `owner_name`, `nickname`, `likes`, `locale` |
| `care.state` | `happiness`, `energy`, `bond`, `streak_days`, `updated_at` |
| `achieve.unlock` | `code`, `title` — once when a badge is earned |
| `alarm.state` | `set`, `hour`, `minute`, `repeat` — robot clock; never a server cron |
| `settings.state` | `volume`, `brightness`, `theme`, `press_to_talk`, `firmware_version`, `can_upgrade` |

**Browser → server**

| `type` | Fields |
|--------|--------|
| `command.dance` | `action` — allowlist: walk, jump, swing, moonwalk, bend, shake_leg, updown, sit, showcase, home |
| `command.stop` | none — `self.otto.stop`, abort if speaking or thinking |
| `game.start` | `game: rps`, optional `best_of` (default 3) — resets the match and Mickey starts the first chant |
| `game.round` | `game: rps` — start the next chant without resetting the score |
| `game.move` | `game: rps`, `player`: rock \| paper \| scissors — only during `countdown`; both throws reveal together after the chant |
| `chat.send` | `text` — typed line; empty/whitespace is `error.invalid` |
| `memory.get` | none |
| `memory.set` | optional `owner_name`, `nickname`, `likes` (capped; stored in Postgres / in-memory stub) |
| `care.action` | `kind`: `pet` \| `feed` — works while Mickey is offline |
| `alarm.get` | none — read the robot wake clock |
| `alarm.set` | `hour` 0–23, `minute` 0–59, `repeat` bool (default true), optional `sleep_now` |
| `alarm.cancel` | none |
| `sleep.now` | optional `hour`, `minute` — empty uses the stored alarm |
| `settings.get` | none — volume / brightness / theme from `self.get_device_status` |
| `settings.set` | optional `volume`, `brightness`, `theme`, `press_to_talk`, optional `trims` (`left_leg` / `right_leg` / `left_foot` / `right_foot`, -50 to 50) |
| `settings.reboot` | none — `self.reboot` (confirm in the UI) |
| `settings.upgrade` | none — server supplies `FIRMWARE_URL`; refused if unpublished (`0.0.0` / `none.bin`); no client `url` or `force` |

If the device session is absent, commands fail with `offline` and presence shows “Wake Mickey (button or wake word), then play.” After `sleep.now` / `alarm.set sleep_now` the session may still be open: presence stays connected with `sleeping: true` and hint “Mickey is sleeping…” until the device socket closes (do not fake `offline` while the session is live). A dashboard viewer holds the device idle timeout so a silent READY session is not closed mid-game. Game rules run on the server; Gemini is not in this path.

RPS is chant-first on the existing companion pipe: `game.start` (or `game.round`) broadcasts `phase: countdown` with both throws hidden while the device speaks `tts` “Rock… Paper… Scissors!” on `/xiaozhi/v1/`. The owner taps `game.move` *during* that chant. When countdown TTS finishes, the hub broadcasts the result `game.state`; Mickey’s jump/sit/home reaction and a win/lose/draw `tts` line start on the same tick, then `home` returns him to stand. First to 2 of 3 wins; `match_over` + `match_winner` end the match until `game.start`.

Phone chat reuses the device Gemini Live socket (`send_client_content`, no second session and no uplink PCM). `chat.send` echoes `chat.user` immediately, then `companion_action("chat")` injects the typed line. THINKING or SPEAKING refuses a new chat (`busy` — tap Stop). Chat is also capped at 10 messages/minute (`COMPANION_CHAT_RATE_LIMIT_PER_MINUTE`). Transcripts stay in RAM (last 10) for open dashboard tabs.

Owner memory is per `device_id` + `client_id` and is injected into Gemini `system_instruction` at Live configure (not by mutating the global prompt string). Care meters decay from a lifespan `asyncio` task about every 8 minutes. Achievements are an event log (`first_activate`, `first_web_dance`, `first_rps_win`, `chat_streak_3`, `first_pet`).

Alarm and settings are device MCP on the same companion pipe. `alarm.*` maps to `self.mickey.alarm.*` / `self.mickey.sleep.now`; the robot clock is source of truth (offline → `error.offline`, no server-side cron). `settings.set` uses volume/brightness/theme/press-to-talk plus optional `trims`. `settings.reboot` and `settings.upgrade` list user-only tools with `withUserTools: true` on a separate map (never `gemini_tools`). Upgrade never accepts a URL from the browser.

---

## 13. Implementation checklist (VPS Python agent)

1. HTTP `GET`+`POST` `/xiaozhi/ota` and `/xiaozhi/ota/` returning the JSON in §2.3 with this VPS WebSocket URL.
2. WebSocket `/xiaozhi/v1/` and `/xiaozhi/v1` accepting the headers in §3.1.
3. Hello handshake §3.2–3.3.
4. Parse listen/abort/mcp; decode Opus; server VAD; Gemini Live STT.
5. MCP initialize + tools/list + tools/call for **every** §5 tool you intend to use (at least otto action/stop + volume).
6. LLM with **server** tools weather/news/music/knowledge.
7. TTS cycle §3.6 + downlink Opus.
8. Emotion `llm` messages.
9. Optional MQTT/UDP §4.
10. TLS (`wss` / `https`) and a firewall allowing 443 or 8000.

Reference client sources (do not copy cloud servers):

- `main/ota.cc`
- `main/protocols/websocket_protocol.cc`
- `main/protocols/mqtt_protocol.cc`
- `main/protocols/protocol.cc`
- `main/application.cc` (`OnIncomingJson`)
- `main/mcp_server.cc` / `main/mcp_server.h`
- `main/boards/mickey/otto_controller.cc`
- `main/boards/mickey/mickey_alarm.cc`
- `docs/websocket.md`, `docs/mqtt-udp.md`, `docs/mcp-protocol.md`

---

## 14. What this backend must not do

- Do not require pin changes. Live OTA identity is `mickey`; still accept legacy `otto-robot` / `phoe-lone`.
- Do not send `type: iot` (deprecated).
- Do not call `self.chassis.*` / `self.dog.*` / `self.electron.*` on Phoe Lone; those belong to other boards.
- Do not block waiting for IMU/light/touch hardware; `wired: false` stubs remain valid.
- Do not put Python FastAPI sources in this ESP-IDF repository; deploy them on the VPS only.
