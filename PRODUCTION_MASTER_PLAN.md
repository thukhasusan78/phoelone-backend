# Phoe Lone Production Master Plan

**Status:** planning document only. No application code (Python or C++) is changed by this file.  
**Date:** 2026-08-23  
**Scope:** ESP32 client ([thukhasusan78/phoelone](https://github.com/thukhasusan78/phoelone), `otto-robot` board) + this FastAPI backend.  
**Goal:** move from a working voice+servo demo to a consumer-ready, EMO-parity desk robot.

This document is the architectural source of truth for the next phases. Implement in the order given. Do not skip P0 safety items.

---

## 0. How to read this plan

| Section | Purpose |
|---------|---------|
| [1. Current state](#1-current-state) | What already works |
| [2. Sensor investigation](#2-sensor-investigation) | MPU6050 / touch / light: code vs wiring |
| [3. Keepalive / ping](#3-keepalive--ping-system) | Why the client logs `unknown type`, and the production design |
| [4. Communication contracts](#4-communication-contracts-for-new-features) | Exact JSON/MCP shapes ESP32 ↔ backend will use |
| [5. GPIO and hardware rules](#5-gpio-and-hardware-rules) | What is already occupied; proposed new pins |
| [6. Roadmap](#6-roadmap) | P0 / P1 / P2 with owners (client vs server) |
| [7. OTA pipeline](#7-ota-pipeline) | From dummy 404 to signed production updates |
| [8. Acceptance](#8-acceptance-criteria) | Done-when checklists |

**Invariant:** existing voice, VAD, TTS, local music, and Otto motion must keep working. New features ride MCP + local tasks; they must not block the audio loop.

---

## 1. Current state

### 1.1 Complete (demo-grade)

- OTA HTTP `GET`/`POST` `/xiaozhi/ota/` returns WebSocket URL, token, `server_time`. MQTT is omitted on purpose.
- WebSocket `/xiaozhi/v1/`: hello, raw Opus v1 (16 kHz up / 24 kHz down), listen/abort/MCP.
- Server Silero VAD (device often never sends `listen/stop`).
- Gemini Live STT + Edge TTS (Burmese) + paced Opus downlink.
- Device MCP: volume, brightness, theme, Otto walk/dance/stop/trim/status/IP, battery read.
- Host tools: weather, news, web, local music, datetime, optional email.
- Abort cuts TTS and music.
- Compose stack: Caddy TLS, Postgres, Redis, `/health` `/ready` `/metrics`.

### 1.2 Partial

- Emotions: `set_emotion` → `type: llm` during a turn; idle face is local (`staticstate` / `neutral`).
- Battery: readable via MCP; no low-voltage policy, no proactive `alert`.
- Wi-Fi reconnect: network recovers; conversation and Gemini context do not.
- Keepalive: 30 s JSON `type: ping` keeps the socket alive as a side effect; client logs it as unknown.
- Servo hold-after-stop: patches live in this repo under `firmware/otto-robot/patches/`; GitHub `otto_controller.cc` still `vTaskDelete`s the action task.
- Music: full-track playback on the TTS path; speech cannot barge in (device is in Speaking, uplink dropped). Wake-word / button abort still works if AFE wake word is enabled.

### 1.3 Architectural constraint (EMO)

XiaoZhi is **session-based**: wake/button → `OpenAudioChannel()` → talk → close → idle.  
EMO is **always present**. Idle life (breathing, looking around, sensor reactions) must run **on the ESP32 without a cloud session**. The WebSocket is for voice and for optional cloud reactions, not for the heartbeat of the body.

---

## 2. Sensor investigation

### 2.1 Verdict (one paragraph)

**Nothing is wired in firmware.** The no-camera Otto `HardwareConfig` has `i2c_sda_pin = GPIO_NUM_NC` and `i2c_scl_pin = GPIO_NUM_NC`. There are **no** MPU6050, light, or touch GPIO `#define`s. The client exposes three MCP **stubs** that return immediately with `wired: false` and never touch I2C, ADC, or GPIO. The backend catalogs those same tool names, tells Gemini not to invent readings, and **drops** device-initiated MCP notifications. To use the hardware you have ready, you must: (1) pick free GPIOs, (2) replace the stubs with real drivers + a local sensor task, (3) teach the backend to handle `notifications/*` and to treat `wired: true` payloads as real.

### 2.2 Client-side: what exists

**File:** `main/boards/otto-robot/otto_controller.cc` (end of `RegisterMcpTools`).

Three tools, always registered, always the same JSON strings:

| MCP tool | Current return (verbatim intent) |
|----------|----------------------------------|
| `self.phoe_lone.imu.get_reading` | `{"wired":false,"sensor":"MPU6050","reason":"I2C pins are GPIO_NUM_NC on otto-robot no-camera"}` |
| `self.phoe_lone.light.get_level` | `{"wired":false,"sensor":"light","reason":"no light-sensor GPIO in stock otto-robot config"}` |
| `self.phoe_lone.touch.get_state` | `{"wired":false,"sensor":"touch","reason":"no touch GPIO in stock otto-robot config"}` |

There is **no driver**, **no FreeRTOS sensor task**, **no interrupt**, **no NVS calibration**, and **no pin in `config.h`**.

`HardwareConfig` only has I2C pins for the **camera** variant (`SDA=15`, `SCL=16`). The **no-camera** variant (this robot) sets both to `NC`. Those camera I2C pins are **speaker BCLK/LRCK (15/16)** on this board — they must never be reused for the MPU6050.

### 2.3 Client-side: pins already occupied (no-camera Otto)

This is the live Phoe Lone profile (`NON_CAMERA_VERSION_CONFIG` + GOAL.md). **Do not change these.**

| Function | GPIO |
|----------|------|
| Boot button | 0 |
| LCD backlight | 3 |
| Mic WS / SCK / DIN | 4 / 5 / 6 |
| Speaker DOUT / BCLK / LRCK | 7 / 15 / 16 |
| LCD MOSI / CLK / DC / RST / CS | 10 / 9 / 46 / 11 / **12** |
| Left leg / left foot | 17 / 18 |
| Right foot / right leg | 38 / 39 |
| Charge detect | 21 |
| Battery ADC (ADC2_CH3) | **14** (implied by `ADC_UNIT_2` + `ADC_CHANNEL_3` on ESP32-S3) |
| Hand left / hand right (firmware only) | **8 / 12** |

**Conflict:** GPIO 12 is both LCD CS and `right_hand_pin`. Firmware sets `has_hands_ = true` because neither hand pin is `NC`. A hand MCP action can PWM the display chip-select. P0 requires treating hands as absent on this 4-servo build.

ESP32-S3 N16R8 octal flash/PSRAM typically occupies **GPIO 26–37**. USB Serial-JTAG uses **19/20**. UART0 monitor on many S3 modules is **43 (TX) / 44 (RX)**.

### 2.4 Proposed new pins (confirm on the bench before soldering if not already committed)

These are **free on the no-camera map**, I2C-capable, and do not collide with octal PSRAM, USB, or the current audio/display/servo set.

| Sensor | Role | Proposed GPIO | Why |
|--------|------|---------------|-----|
| MPU6050 | I2C SDA | **41** | Free (camera-board I2S pins; unused here) |
| MPU6050 | I2C SCL | **42** | Same |
| MPU6050 | INT (optional but recommended) | **40** | Motion/fall without 100 Hz polling |
| Light | Prefer I2C (BH1750 / VEML7700) on **same 41/42 bus** | — | One bus, no extra ADC vs Wi-Fi (ADC2 is already used for battery) |
| Light (fallback analog LDR) | ADC1 | **1** (ADC1_CH0) | ADC1 is safer than ADC2 while Wi-Fi is on |
| Touch | Digital TTP223 / similar | **47** | Simple 3.3 V digital, ISR-friendly |
| Touch (fallback capacitive) | Touch pad | **2** (TOUCH2) | Only if no TTP223 |

**Must confirm with the physical wiring** you already prepared. If the sensors are already soldered to other free pins (13, 45, 48, 1, 2), put those numbers into `config.h` instead of inventing a second move. The plan cares that pins are **named, documented, and not in the occupied table** — not that they are exactly 41/42/40/47.

**Electrical rules**

- MPU6050 at 3.3 V (not 5 V). Shared GND with ESP32. 4.7 kΩ pull-ups on SDA/SCL to 3.3 V if the module does not include them.
- Do not power servos and sensors from the ESP32 3.3 V pin if current is tight; use the board 3.3 V rail.
- Light analog divider (if used) must stay within 0–3.3 V.
- Touch digital output must be 3.3 V logic. If the module is 5 V, level-shift.

### 2.5 Backend-side: what exists

| Location | Behavior |
|----------|----------|
| `app/mcp/catalog.py` | English descriptions for the three `self.phoe_lone.*` tools. Tells Gemini the board is unwired. Listed in `PHOE_LONE_FALLBACK_NAMES`. |
| `app/ai/prompts.py` | “IMU, light, and touch are unwired stubs. Never invent sensor readings.” / “If a tool returns wired:false, say the sensor is not connected yet.” |
| `app/ai/tool_router.py` | Forwards `self.*` to device MCP. No special-case parsing of IMU JSON. |
| `app/mcp/client.py` `on_message` | **Drops** any payload whose `method` starts with `notifications`. Device-initiated events are discarded. |
| No sensor polling loop | Backend never calls IMU/light/touch unless Gemini chooses those tools during a voice turn. |

There is **no** backend logic for pickup, fall, pet, or day/night. There is **no** store of last lux / last accel. Host tools and session code do not subscribe to sensors.

### 2.6 What you need to wire vs what you need to build

| Item | Wire on the bench? | Firmware? | Backend? |
|------|--------------------|-----------|----------|
| MPU6050 SDA/SCL (+ INT if possible) | **Yes — not assigned in code today** | Driver + 50–100 Hz task + MCP + local reactions | Parse `wired:true`; handle notifications; update prompt |
| Light sensor | **Yes** | Driver (I2C or ADC) + MCP + optional night face | Same |
| Touch | **Yes** | GPIO ISR + debounce + MCP + local “pet” animation | Same |
| Pin `#define`s in `config.h` | After pins are chosen | **Yes — currently missing** | No |
| Idle look-around / breathing | N/A | **Yes — does not exist** | Optional cloud mood later |
| `notifications/phoe_lone.event` | N/A | Emit on edge (touch/pickup/fall) | **Must stop dropping notifications** |

### 2.7 Target software architecture (sensors)

```
[MPU6050 / Light / Touch hardware]
        │
        ▼
 SensorTask (own FreeRTOS task, priority < audio)
   - I2C/ADC/GPIO, never on the WS or audio task
   - debounce, complementary filter, thresholds in NVS
   - local reactions immediately (face GIF, tiny motion, sound)
        │
        ├── MCP tools (LLM pull): get_reading / get_level / get_state
        └── MCP notifications (push, no id):
              method: "notifications/phoe_lone.event"
              params: { "event": "pickup"|"putdown"|"fall"|"pet"|"bright"|"dark", ... }

[If WS channel is open]
        └── Protocol::SendMcpMessage(notification)
                    │
                    ▼
 Backend McpClient.on_message  →  DeviceSession._on_sensor_event
                    │
                    ├── optional: set_emotion + short TTS if in listening/idle-open
                    └── optional: inject INTERNAL EVENT into Gemini Live
                        (do not start a full turn on every pet while speaking)
[If WS channel is closed]
        └── Local only. That is enough for EMO idle life.
```

**Rule:** sensor personality must work with the WebSocket **closed**. Cloud is an enhancement (talk about the pet, log events), not a requirement for the robot to flinch.

### 2.8 MCP payload contracts (after wiring)

Replace stub strings with live JSON. Keep `wired` so old prompts still work.

**`self.phoe_lone.imu.get_reading`**

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

`event` is one of: `still`, `moving`, `pickup`, `putdown`, `fall`, `shake`. Units: g and deg/s. If I2C fails: `{"wired":true,"ok":false,"error":"i2c_nack"}`.

**`self.phoe_lone.light.get_level`**

```json
{
  "wired": true,
  "lux": 120,
  "bucket": "indoor",
  "raw": 1840
}
```

`bucket`: `dark` | `dim` | `indoor` | `bright`. Analog fallback may omit `lux` and send only `raw` + `bucket`.

**`self.phoe_lone.touch.get_state`**

```json
{
  "wired": true,
  "touched": true,
  "count": 14,
  "ms_held": 320
}
```

**Notification (device → server, JSON-RPC, no `id`)**

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

Backend must **not** reply. Coalesce (e.g. max 2 events/s). Ignore during `SPEAKING` except `fall` (safety: `self.otto.stop` locally first, then notify).

---

## 3. Keepalive / ping system

### 3.1 What happens today

**Backend** (`app/protocol/messages.py` + `DeviceSession._keepalive_loop`):

- Interval: `keepalive_interval_s = 30`.
- Every 30 s, enqueue:

```json
{ "session_id": "<uuid>", "type": "ping" }
```

- Same loop also: (a) ends a listen after `listen_idle_timeout_s` (8 s) of no uplink; (b) closes the server session after `device_idle_timeout_s` (100 s) of no **inbound** frames unless the state is THINKING or SPEAKING (TTS/music).

**Client** (`Application::OnIncomingJson` in XiaoZhi / phoelone):

Handled types: `tts`, `stt`, `llm`, `mcp`, `system`, `alert`, optional `custom`.  
Anything else: `ESP_LOGW(..., "Unknown message type: %s", type)` and **return**.

`ping` is **not** in that list. That is why the serial monitor shows unknown type every 30 s.

### 3.2 Does ping still do its job?

**Mostly yes, accidentally.**

Device idle timeout is **120 s since last incoming WebSocket frame** (`backend_spec.md`). A text JSON frame, even with an unknown `type`, is still an incoming frame. The ESP32 receive path updates last-rx, then the JSON dispatcher ignores the payload. So `ping` **prevents the 120 s channel close** and **does not** change listening/speaking state. Cost: log spam and no proof that the **application task** (vs the TCP stack) is alive.

Tests already skip pings (`tests/contract/test_websocket.py` `_recv_json`).

### 3.3 What not to do

- Do not send `alert` or `tts` as keepalive (would vibrate the robot or jump to Speaking).
- Do not send empty binary Opus (would hit the decoder).
- Do not rely only on TCP keepalive (too slow; NAT and Caddy idle timeouts differ).
- Do not invent a new top-level `type` that older firmware would also log — unless we ship firmware that handles it in the same release.

### 3.4 Production design (two layers)

Keep both. They solve different failures.

#### Layer A — WebSocket control ping/pong (transport)

- Server (Uvicorn/Caddy): protocol **opcode 0x9 ping** every 20–30 s; expect pong.
- ESP-IDF websocket client answers pong in the stack.
- **Verify in firmware** that control frames reset the 120 s last-incoming timer. If they do **not**, Layer B remains mandatory.
- This survives JSON parser stalls but not a wedged `Application` task.

#### Layer B — Application `ping` / `pong` (what we already almost have)

**Server → device (keep current shape, document it as official):**

```json
{ "session_id": "<id>", "type": "ping", "ts_ms": 1710000000000 }
```

**Client handler (firmware, ~10 lines in `OnIncomingJson`):**

- If `type == "ping"`: do nothing to UI/audio; optionally send:

```json
{ "session_id": "<id>", "type": "pong", "ts_ms": <echo> }
```

- No emotion, no log at WARN. `ESP_LOGD` only.

**Server:**

- Treat inbound `type: pong` as known (today it is `session.unknown_type` on the **backend** receive path — same class of bug, opposite direction).
- If Layer B pongs stop while Layer A still works, log `app_task_unhealthy` and close/reopen policy later.

**Interval:** keep **30 s** JSON ping (half of 120 s, with margin for jitter). Do not go below ~15 s (wakes radio, burns battery).

**Generation / writer queue:** keepalive uses the current session generation. An abort drops queued pings from the old generation; the next 30 s tick sends a fresh one. That is correct. During long music, SPEAKING skips the **server** idle close, but the **device** still needs inbound frames: continue sending ping (or WS ping) during music.

### 3.5 Implementation sequence (keepalive)

1. **Firmware:** silent `ping` handler + optional `pong` (P0, tiny).
2. **Backend:** accept `pong`; stop logging it as unknown (P0).
3. **Ops:** enable Uvicorn/Caddy WS ping; confirm 120 s timer vs control frames on device (P0 verify).
4. **Docs:** add `ping`/`pong` to `backend_spec.md` and phoelone `docs/websocket.md` (P0).
5. Do **not** wait on this to ship sensors; ping is independent.

---

## 4. Communication contracts for new features

All new cloud features use existing envelopes. Do not add `type: iot`. Do not require MQTT.

### 4.1 Already used (do not change)

| Direction | `type` | Role |
|-----------|--------|------|
| both | `hello` | Audio channel open |
| device → server | `listen`, `abort` | Mic / barge-in |
| both | `mcp` | Tools and (soon) notifications |
| server → device | `tts`, `stt`, `llm` | Voice UI |
| server → device | `alert` | Rare UX (battery, TTS fail) |
| server → device | `system` / `reboot` | Reserved; unused today |

### 4.2 New / officially adopted

| Direction | Envelope | When |
|-----------|----------|------|
| server → device | `type: ping` | Every 30 s while WS open |
| device → server | `type: pong` | Optional reply to ping |
| device → server | `mcp` + `notifications/phoe_lone.event` | Touch / pickup / fall / light bucket change |
| server → device | `mcp` `tools/call` on existing `self.phoe_lone.*` | LLM pull |
| server → device | `llm` emotion | Idle mood **only if WS is already open**; idle GIF changes are **local** |
| server → device | `alert` | Low battery, fall, sensor fault |

### 4.3 Sensor events vs voice turns

| Situation | Local ESP32 | Backend |
|-----------|-------------|---------|
| WS closed, user pets head | Face + optional tiny motion + optional OGG | Nothing |
| WS open, listening, pet | Same local + notification | Optional one-shot Gemini INTERNAL EVENT; do not steal the mic |
| WS open, speaking/music, pet | Local only; drop notify or coalesce | Ignore pet |
| Fall / tip-over | **Immediate `self.otto.stop` + home** locally | Notify; `alert` + inhibit motion until IMU stable |
| Light → dark for N minutes | Sleepy GIF, dim backlight (local power save) | Optional |
| Pickup | Pause idle motion; “held” face | Optional surprise line if listening |

### 4.4 Always-on presence (EMO idle) — local protocol, not cloud

Idle director is a firmware module (`PhoeLoneBehavior` or similar):

- Runs only in `kDeviceStateIdle` (WS closed or open-but-idle).
- Every 8–20 s: pick look-left / look-right / blink GIF / 2° servo sway, all **bounded** and **preempted** by wake word, button, touch, pickup.
- Never call the cloud to decide a fidget.
- Cloud may later send `llm` emotion if a session is open; idle director yields to that for 30 s then resumes.

This is P1 and is the highest EMO ROI. It does not depend on sensors, but sensors make it better (look toward a pet, freeze when picked up).

---

## 5. GPIO and hardware rules

### 5.1 Frozen (GOAL.md)

Do not remap: servos 39/38/17/18, display 9/10/11/46/3/(12 CS), amp 16/15/7, mic 6/5/4, boot 0.

### 5.2 P0 firmware pin policy (hands)

Set `left_hand_pin` and `right_hand_pin` to `GPIO_NUM_NC` **or** a compile-time `OTTO_HAS_HANDS=0` so `has_hands_ == false`. GPIO 8 becomes free; GPIO 12 stays display CS only. Hand MCP actions must return the existing error string.

If GPIO 8 is unused after that, it **may** be a sensor pin later. Do not assign it until hands are confirmed NC.

### 5.3 Sensor pin checklist before code

1. Photograph / write the actual SDA, SCL, INT, light, touch nets.
2. Diff against the occupied table in §2.3.
3. Reject: 0, 3–7, 9–12, 14–18, 19–21, 26–39, 43, 44, 46.
4. Commit pins in `config.h` with comments and in this plan’s table.

---

## 6. Roadmap

Owners: **FW** = phoelone ESP-IDF, **BE** = this repo, **HW** = bench wiring, **OPS** = VPS/Caddy.

### P0 — Do not ship without these (safety, identity, “it doesn’t die”)

| ID | Item | Owner | Notes |
|----|------|-------|-------|
| P0.1 | Apply `firmware/otto-robot/patches/001–003` on the flashed tree; confirm GitHub no longer `vTaskDelete`s stop | FW | Sag = hardware failure mode |
| P0.2 | Disable hand servos (`NC` / `has_hands_=false`); never PWM GPIO 12 | FW | Display CS conflict |
| P0.3 | Unique `main/boards/phoe-lone/` board type before real OTA | FW | Spec: do not ship as `otto-robot` identity |
| P0.4 | Low-battery policy: inhibit motion, `low_battery.ogg`, home pose, dim | FW | Optional BE `alert` if status is polled |
| P0.5 | Stop rotating WS tokens on every OTA unless NVS write is guaranteed | BE | Mid-session OTA retry can brick auth |
| P0.6 | Silent `ping` handler + `pong`; BE accept `pong` | FW+BE | §3 |
| P0.7 | Verify WS ping/pong vs 120 s timer; Caddy idle | OPS+FW | |
| P0.8 | Confirm wake-word abort during TTS **and** music | FW | AFE wake word on while speaking |
| P0.9 | Dummy OTA stays 404 in lab; document; no `force:1` | BE | Until §7 |

**P0 sensors (hardware you have ready — start in parallel, do not block P0.1–P0.2):**

| ID | Item | Owner |
|----|------|-------|
| P0.S1 | Confirm physical pin list; write into `config.h` | HW+FW |
| P0.S2 | MPU6050 I2C bring-up: whoami `0x68`/`0x69` in serial; MCP `wired:true` | FW |
| P0.S3 | Touch GPIO ISR + 30–50 ms debounce; MCP + local pet GIF | FW |
| P0.S4 | Light driver (I2C preferred); MCP buckets | FW |
| P0.S5 | BE: stop dropping `notifications/*`; parse `phoe_lone.event` | BE |
| P0.S6 | Prompt: sensors **are** wired; still do not invent numbers; fall → stop | BE |
| P0.S7 | Local fall: stop servos **before** any network | FW |

P0.S* can land as “sensors work locally + MCP pull” even before idle director (P1).

### P1 — Feels like a robot, not a speaker

| ID | Item | Owner |
|----|------|-------|
| P1.1 | On-device idle director (fidget, blink, preempt) | FW |
| P1.2 | Emotion → short motion table (happy/swing, sad/sit, …) capped so audio never waits | FW+BE prompt |
| P1.3 | Idle GIF cycle (`staticstate`, `sleepy`, `winking`) | FW |
| P1.4 | Pickup: freeze idle; putdown: resume | FW |
| P1.5 | Dark bucket → sleepy + dim; bright → wake face | FW |
| P1.6 | Music: keep abort; optional canned dance while track plays | FW+BE |
| P1.7 | Proactive battery / Wi-Fi `alert` | BE |
| P1.8 | Optional: Gemini INTERNAL EVENT on pet **only in listening** | BE |

P1 does **not** require a persistent WS in idle. Optional later: lightweight heartbeat WS for cloud mood; that is P2.

### P2 — EMO-level and productization

| ID | Item | Owner |
|----|------|-------|
| P2.1 | Time-of-day sleep / morning greeting | FW+BE |
| P2.2 | Persistent memory (owner name, likes) in Postgres → system prompt — **BE shipped** (dashboard Settings + Live inject) | BE |
| P2.3 | Signed OTA, dual-bank, per-board channel `phoe-lone` | FW+BE+OPS |
| P2.4 | Server AEC + protocol v2 + `listen: realtime` (hardware AEC still preferred) | FW+BE |
| P2.5 | Companion pairing app (user-only MCP) — **BE dashboard shipped**; cookie + optional `COMPANION_PIN`; no native phone app | BE+app |
| P2.6 | Crash dump upload, per-MAC metrics | FW+BE |
| P2.7 | Myanmar glyph-push / font | FW+BE |
| P2.8 | Camera / ToF face-proximity — **only if** that hardware exists | HW+FW |
| P2.9 | Factory fixture: servo sweep, mic loopback, sensor whoami, ADC | FW |
| P2.10 | Gemini/TTS spend caps per device | BE |

Out of scope unless explicitly required: 4G, MQTT voice path, smart-home cloud MCP, copying LivingAI assets/APIs.

---

## 7. OTA pipeline

### 7.1 Today

- Device `CheckVersion()` POSTs to `CONFIG_OTA_URL` (currently a hardcoded VPS in `otto-robot/config.json`).
- Backend returns `firmware.version = 0.0.0` and `url = …/firmware/none.bin` which **404**s so the client skips upgrade.
- Client **can** download, write ota partition, reboot (`UpgradeFirmware`). User-only MCP `self.upgrade_firmware` exists but is hidden from the LLM.

### 7.2 Production target

1. Board identity `phoe-lone` in OTA POST `board.type`.
2. Backend selects artifact: `{board, current_version, elf_sha256}` → signed URL.
3. HTTPS only. `force: 0` unless a factory brick recovery.
4. Image: ESP-IDF app + matching partition table; version **newer** than running.
5. Device: download with progress UI, `MarkCurrentVersionValid` after boot, rollback if crash loop (IDF app rollback).
6. Backend: never rotate WS token in the same OTA response that also starts a firmware download without documenting order (token write in NVS happens during OTA JSON parse **before** the binary download). Prefer: token stable across version checks; rotate only via `phoe-lone rotate` CLI.
7. Lab: keep `0.0.0` + 404. Staging: real bucket. Prod: signed objects, immutable versions.

---

## 8. Acceptance criteria

### Sensors (P0.S)

- [ ] Serial: MPU6050 WHO_AM_I ok; touch edges; light buckets change with a flashlight.
- [ ] MCP pull returns `wired: true` and plausible numbers (not zeros-only unless still).
- [ ] Unplugging I2C returns `ok: false`, not invented g-values.
- [ ] Pet: face changes within 200 ms with **WS closed**.
- [ ] Fall / tip: servos stop within 200 ms locally.
- [ ] Audio/wake-word still works; no watchdog during I2C.
- [ ] Backend logs `phoe_lone.event` instead of dropping notifications.

### Keepalive (P0.6–P0.7)

- [ ] No `Unknown message type: ping` at WARN on device.
- [ ] Session stays open > 3 minutes of silence (listening or after tts/stop auto-listen) without 120 s drop.
- [ ] Music of 4+ minutes does not die from device idle timeout.
- [ ] Backend does not treat `pong` as unknown.

### Safety (P0.1–P0.4)

- [ ] `self.otto.stop` holds pose (no sag).
- [ ] Hand tools error; GPIO 12 never PWM’d as a servo.
- [ ] Low battery: no walk/jump.

### EMO P1

- [ ] Idle fidget visible on the desk with nobody talking.
- [ ] Wake word immediately cancels fidget.
- [ ] Pickup freezes motion; pet plays a reaction.

---

## 9. File map (when implementation starts)

Do not implement in this planning pass. For later agents:

**Firmware (phoelone)**

- `main/boards/otto-robot/config.h` — pin `#define`s; hands NC.
- `main/boards/otto-robot/otto_controller.cc` — replace stubs; stop patch.
- New: `phoe_lone_sensors.{h,cc}`, `phoe_lone_behavior.{h,cc}`.
- `main/application.cc` — `ping`/`pong` only (minimal).
- Later: `main/boards/phoe-lone/` copy.

**Backend (this repo)**

- `app/mcp/client.py` — handle notifications.
- `app/sessions/session.py` — `_on_sensor_event`, `pong`.
- `app/protocol/messages.py` — optional `pong` helper; ping may add `ts_ms`.
- `app/mcp/catalog.py` + `app/ai/prompts.py` — wired sensors.
- `app/auth/service.py` — stop unconditional token rotate.
- `backend_spec.md` — ping/pong + notification schema.

**Tests**

- Contract: ping then pong.
- Unit: notification not dropped; stub vs wired JSON.
- Do not require hardware in CI; fake I2C in firmware tests if any.

---

## 10. Decision log

| Decision | Choice | Reason |
|----------|--------|--------|
| Idle life | On-device | WS session model cannot be EMO |
| Sensor cloud path | MCP notify + pull tools | Already the IoT channel |
| Light sensor | I2C on MPU bus preferred | ADC2 + Wi-Fi conflict; battery already on ADC2 |
| Keepalive | Keep JSON `ping`; add client handler; add WS opcode ping | JSON already resets 120 s; handler removes log lie |
| Hands | Disabled on this SKU | GPIO 12 = CS |
| OTA identity | New `phoe-lone` board before real binaries | Avoid otto-robot channel collisions |
| Camera / face track | P2, hardware-gated | This SKU is no-camera |

---

## 11. First implementation slice (when coding is allowed)

Suggested first PR pair (still not done in this document’s change set):

1. **FW:** hands NC + stop patches + `ping` handler.  
2. **FW:** `config.h` pins + MPU6050 whoami + MCP `wired:true`.  
3. **BE:** notification handler + prompt + pong.  
4. **FW:** touch + light + local pet/fall.  
5. **FW:** idle director.

Stop after each slice and run the matching acceptance list.
)
