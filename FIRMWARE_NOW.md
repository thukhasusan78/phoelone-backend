# Mickey firmware sprint — do this now

**Status:** implementation brief for the ESP32 repo. Do not implement C++ in this FastAPI tree.  
**Date:** 2026-09-02  
**Firmware repo:** [thukhasusan78/phoelone](https://github.com/thukhasusan78/phoelone)  
**Board:** `mickey` (ESP32-S3 N16R8). Build: `python scripts/build.py mickey --name mickey --language en-US`  
**OTA:** `https://phoelone.thukha.online/xiaozhi/ota/` baked in `main/boards/mickey/config.json`  
**Wire contract:** [backend_spec.md](backend_spec.md) (frozen). Longer GPIO / sensor notes: [CLIENT_PRODUCTION_PLAN.md](CLIENT_PRODUCTION_PLAN.md). Companion Phase 6: [COMPANION_ROADMAP.md](COMPANION_ROADMAP.md) §7.1.

The VPS brain and companion dashboard (chat, care, RPS, tic-tac-toe, alarm, settings) are **ahead of the robot**. This sprint makes Mickey safe, alive on the desk, and reachable from the phone without a wake word.

**Invariant:** voice, wake word, Opus, Otto MCP, GIFs, alarm/sleep MCP, and the existing hello/listen/abort path must keep working. New work runs on **separate FreeRTOS tasks below audio priority**.

Do not skip Slice 1. Stop after each slice and run that slice’s acceptance list.

---

## 0. Why this order

The cloud already:

- Sends JSON `type: ping` every 30 s (including during TTS/music) with `ts_ms`.
- Accepts `type: pong` (not required; old firmware stays valid).
- Routes `notifications/phoe_lone.event` (`pet` / `pickup` / `putdown` / `fall` / `sleep`). Pet while listening can speak a short Burmese line. Fall inhibits Otto motion for 5 s except `self.otto.stop`.
- Skips the ~100 s device-idle close **while a dashboard tab is open** — that cannot invent a socket if Mickey never connected.

Firmware still typically:

- Logs `Unknown message type: ping` every 30 s.
- May `vTaskDelete` on `self.otto.stop` → servo sag.
- Treats GPIO 12 as a hand servo **and** LCD CS.
- Returns IMU/light/touch as `wired: false`.
- Sits still in idle (XiaoZhi session model: wake → talk → close → statue).
- Opens `/xiaozhi/v1/` only for a voice turn → dashboard says “Wake Mickey first.”

Idle life and pet/fall **must work with the WebSocket closed**. Cloud is an enhancement.

```
Slice 1  safety (stop hold + hands NC + low battery)
Slice 2  ping → pong (silence WARN; prove app task alive)
Slice 3  sensors local + notify (MPU / touch / light)
Slice 4  idle director (EMO fidget, no cloud)
Slice 5  always-on WS (companion play without wake)
```

Slice 5 depends on Slice 2. Slices 3–4 do not depend on Slice 5. Sensors may wait on a pin photo if modules are not soldered yet — do not invent GPIOs.

---

## 1. Frozen hardware (do not remap)

| Function | GPIO |
|----------|------|
| Boot button | 0 |
| LCD backlight | 3 |
| Mic WS / SCK / DIN | 4 / 5 / 6 |
| Speaker DOUT / BCLK / LRCK | 7 / **15** / **16** |
| LCD MOSI / CLK / DC / RST / CS | 10 / 9 / 46 / 11 / **12** |
| Left leg / left foot | 17 / 18 |
| Right foot / right leg | 38 / 39 |
| Charge detect | 21 |
| Battery ADC2_CH3 | **14** |

Reject for new sensors: `0`, `3–7`, `9–12`, `14–18`, `19–21`, `26–39`, `43`, `44`, `46`. Camera-board I2C 15/16 is **speaker BCLK/LRCK** on this SKU — never reuse.

**Proposed sensor pins** (confirm on the bench; if already soldered to other *free* pins, use those):

| Sensor | GPIO |
|--------|------|
| MPU6050 SDA / SCL | **41** / **42** |
| MPU6050 INT (optional) | **40** |
| Light (BH1750 / VEML7700) | same 41/42 |
| Light fallback LDR | **1** (ADC1 only; not ADC2) |
| Touch TTP223 | **47** (3.3 V digital) |

MPU at **3.3 V**, shared GND, 4.7 kΩ pull-ups if the module has none. Servos stay on external 5 V.

---

## 2. What the VPS already speaks (copy these shapes)

Do not add `type: iot`. Do not require MQTT. Do not wait for a Python change.

### 2.1 Keepalive

Server → device every ~30 s:

```json
{ "session_id": "<hello uuid>", "type": "ping", "ts_ms": 1710000000000 }
```

Device → server (Slice 2):

```json
{ "session_id": "<hello uuid>", "type": "pong", "ts_ms": 1710000000000 }
```

No emotion, no TTS, no MCP. `ESP_LOGD` only. Echo `ts_ms` if present.

### 2.2 Sensor pull (after Slice 3)

Keep `wired` forever. Until drivers work, **keep** `wired: false` stubs.

Live IMU success: `wired: true`, `ax/ay/az` (g), `gx/gy/gz` (deg/s), `pitch`, `roll`, `temp_c`, `event` one of `still` | `moving` | `pickup` | `putdown` | `fall` | `shake`.  
I2C fail: `{ "wired": true, "ok": false, "error": "i2c_nack" }` — never fake 1 g.

Light: `{ "wired": true, "lux": 120, "bucket": "indoor", "raw": 1840 }`  
`bucket`: `dark` | `dim` | `indoor` | `bright`. Analog fallback may omit `lux`.

Touch: `{ "wired": true, "touched": true, "count": 14, "ms_held": 320 }`.

### 2.3 Sensor push (no JSON-RPC `id`)

Only if the audio channel is open. **No reply expected.** Coalesce max **2 events/s**. During speaking/music send **`fall` only** (drop `pet`). Do **not** emit `bright` / `dark` in this sprint (backend_spec: firmware does not emit those).

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
      "imu": { "pitch": 8.0, "az": 0.2 }
    }
  }
}
```

`event`: `pickup` | `putdown` | `fall` | `pet` | `sleep` (sleep already used ~400 ms before socket close).

Fall: **stop servos locally first**, then notify if WS open.

---

## 3. Slice 1 — Safety (must flash first)

**Owner:** firmware. **Goal:** stop does not sag; GPIO 12 is never a servo; low battery does not walk.

### 3.1 Cooperative stop (P0.1)

Patches live in this repo: [`firmware/otto-robot/patches/`](firmware/otto-robot/patches/). Apply in the **phoelone** tree. Live files are under `main/boards/mickey/` (legacy patches say `otto-robot/` — retarget the path if `patch -p1` misses).

| Patch | Effect |
|-------|--------|
| `001-oscillator-keep-hold.patch` | Re-`Attach` must not `ledc_stop`; refresh `Write(pos_)` |
| `002-home-force-stance.patch` | `Home()` always reapplies 90° even if resting |
| `003-stop-cooperative-home.patch` | Stop = flag + drain queue + `ACTION_HOME`; **no** `vTaskDelete` |

Action loops abort early when `stop_requested_`. Trims in NVS unchanged.

### 3.2 Hands NC (P0.2)

Set `left_hand_pin` and `right_hand_pin` to `GPIO_NUM_NC` **or** `OTTO_HAS_HANDS=0` so `has_hands_ == false`. GPIO 12 stays **LCD CS only**. Hand MCP tools keep returning the existing “needs hand servos” error. Do not assign GPIO 8 to a sensor until this is flashed and the display still works.

### 3.3 Low battery (P0.4)

`PowerManager` already polls ADC and GPIO 21. If level &lt; ~15% (NVS-tunable) and not charging:

- Reject `QueueAction` except home/stop.
- Play low-battery OGG at most once per N minutes.
- Dim backlight; `SetEmotion("sleepy")`.
- No walk / jump / showcase.

### Acceptance (Slice 1)

- [ ] `self.otto.stop` (voice, dashboard Stop, or MCP) holds pose ≥ 30 s with 5 V on the servos.
- [ ] Hand tools error; LCD never glitches when a hand action is requested.
- [ ] Below threshold: walk/jump refused; face sleepy.

---

## 4. Slice 2 — JSON ping / pong (P0.6)

**File:** `main/application.cc` `OnIncomingJson`, **before** the unknown-type WARN.

If `type == "ping"`:

1. Do not change device state, emotion, or audio.
2. Schedule send of `type: pong` with the hello `session_id` and echoed `ts_ms`.
3. Return.

**Also verify (P0.7):** whether WebSocket opcode 0x9 ping updates XiaoZhi’s **120 s** last-incoming timer (`websocket_protocol.cc`). If only TEXT/BINARY reset it, JSON ping remains mandatory. Document in `docs/websocket.md`.

**Do not** handle ping with `alert`, TTS, or empty Opus.

### Acceptance (Slice 2)

- [ ] Serial: no `Unknown message type: ping` at WARN.
- [ ] Optional `pong` visible in server logs (`session` receive path, not `unknown_type`).
- [ ] Channel stays open &gt; 3 minutes of silence (listening or after `tts/stop` auto-listen).
- [ ] Local music / long TTS ≥ 4 minutes does not die from the device 120 s timer (server already keeps pinging during SPEAKING).

---

## 5. Slice 3 — Sensors local + notify (P0.S)

**Hard rule:** personality works with WS **closed**. Notifications are best-effort.

### 5.1 Pins

Photograph SDA/SCL/INT/light/touch. Diff against §1. Commit `#define`s in `main/boards/mickey/config.h` (e.g. `PHOE_LONE_IMU_SDA`). First boot with **servo 5 V unpowered** until WHO_AM_I succeeds.

If modules are not on the desk yet: keep stubs, skip to Slice 4, return here.

### 5.2 Architecture

New files (suggested): `main/boards/mickey/phoe_lone_sensors.h/.cc`.

```
SensorTask  (priority < audio encoder, stack ~4–6 KB)
  loop 20–50 ms (or block on INT + 20 Hz poll)
    I2C mutex: MPU + light
    touch: ISR sets flag; task debounces 30–50 ms
    classify IMU / light bucket / touch edge
    --- local, never wait on WS ---
    fall: OttoStopAndHome() immediately
    pet / pickup: SetEmotion + optional OGG; pause idle director
    --- if audio channel open ---
    SendMcpMessage(notification), no id
MCP get_reading / get_level / get_state: memcpy last snapshot
```

Never I2C from the WebSocket or audio callback. Watchdog: the task must delay or block, not spin.

MPU bring-up: 100 kHz first; probe `0x68` then `0x69`; WHO_AM_I in serial; wake `PWR_MGMT_1`; ±2 g / ±250 dps. Classify `fall` as tilt ≳ 55° for &gt; 150 ms or `|az|` far from 1 g with large gyro (tune in NVS). Unplug → `ok: false`, not a frozen 1 g.

### 5.3 Local reactions vs cloud

| Situation | Firmware |
|-----------|----------|
| WS closed, pet | Face + optional tiny motion/OGG |
| WS open, listening, pet | Same + notify |
| WS open, speaking/music, pet | Local only; no pet notify |
| Fall | Stop+home **&lt; 200 ms**, then notify if WS open |
| Pickup | Pause idle director; held face |

Do **not** start a cloud voice turn from a pet. The VPS may speak one line if listening; duplicate Otto dance from the device fights the idle director.

### Acceptance (Slice 3)

- [ ] Serial: MPU WHO_AM_I ok; touch edges; light buckets change with a flashlight.
- [ ] MCP pull `wired: true` and plausible numbers.
- [ ] Unplug I2C → `ok: false`.
- [ ] Pet: face changes &lt; 200 ms with **WS closed**.
- [ ] Fall: servos stop &lt; 200 ms **before** any notify.
- [ ] VPS journal: `session.phoe_lone_event` for pet/fall (when WS open).
- [ ] Audio / wake word still clean; no watchdog during I2C.

---

## 6. Slice 4 — Idle director (P1.1–P1.5)

Highest EMO ROI. **No cloud.** New `phoe_lone_behavior.h/.cc`.

- Runs in `kDeviceStateIdle` (typical: WS closed). v1: **off while the mic is hot** (servo noise vs VAD).
- Every **8–20 s** (random): blink GIF (`staticstate` / `winking` / `sleepy`) **or** 1–2° look, duration &lt; 800 ms, then home. Queue depth **1**. Bound speed.
- Preempt immediately: wake word, GPIO 0, pet, pickup, fall, `OpenAudioChannel`, any `self.otto.action`.
- Incoming `type: llm` emotion: show that GIF ~30 s, then resume the cycle.
- After sensors: pickup freezes fidget; putdown resumes after ~1 s stable; dark bucket → sleepy + dim (local only; still no `bright`/`dark` notify this sprint).

Do **not** call the LLM to pick a fidget. Do **not** auto-dance to music unless MCP asked.

### Acceptance (Slice 4)

- [ ] Visible fidget on the desk with nobody talking.
- [ ] Wake word / button cancels fidget immediately.
- [ ] Pickup (if IMU live) freezes motion; pet still plays a reaction.

---

## 7. Slice 5 — Always-on companion socket (Phase 6a)

**Goal:** open https://phoelone.thukha.online/ and play dance / RPS / TTT / chat without a wake word.

Today the device opens `/xiaozhi/v1/` for a **voice** session. The dashboard cannot move the body if `SessionManager` has no session.

Firmware:

- Stay connected in idle, **or** reconnect automatically, without being in a listen/speak turn.
- Keep answering JSON `ping` with `pong` (Slice 2).
- Idle director still runs locally and is preempted by MCP / wake / pet / fall.
- Do not send uplink Opus in this idle-connected mode unless the user wakes or the server starts TTS (existing speaking path).
- Sleep (`self.mickey.sleep.now`) still emits `event: sleep` then closes; dashboard already shows “Mickey is sleeping.”

Do **not** use the idle socket to request fidget decisions from Gemini.

**Not in this slice:** LCD `preview_image` / RPS icons (Phase 6b), signed OTA `.bin`, protocol v2 / server AEC.

### Acceptance (Slice 5)

- [ ] Power on, no wake word: dashboard presence **Online** within a few seconds of Wi-Fi.
- [ ] Dance pad / TTT / chat work from the phone while Mickey is idle-connected.
- [ ] Wake word still starts a normal listen turn.
- [ ] Sleep now → dashboard sleeping hint; wake/button restores Online.

---

## 8. Out of this sprint

| Item | Why later |
|------|-----------|
| Copy board to `main/boards/phoe-lone/` | Live identity is already `mickey`; do this only before a **real** OTA `.bin` if you still want that name |
| Signed OTA / dual-bank product channel | Lab must stay `0.0.0` + 404 `none.bin`, `force: 0` |
| `preview_image` LCD icons | Needs firmware snapshot + static assets on the VPS |
| `bright` / `dark` notifications | Spec currently forbids them; local dim is enough |
| Camera / ToF | This SKU is no-camera |
| MQTT voice, 4G, LivingAI APIs | Out of scope |
| Simon / trivia on the dashboard | Python; not firmware |

---

## 9. File map (phoelone tree)

| Path | Slice |
|------|--------|
| `main/boards/mickey/config.h` | 1 hands NC; 3 sensor pins |
| `main/boards/mickey/oscillator.cc` | 1 patch 001 |
| `main/boards/mickey/otto_movements.cc` | 1 patch 002 |
| `main/boards/mickey/otto_controller.cc` | 1 patch 003; 3 MCP live sensors |
| `main/boards/mickey/phoe_lone_sensors.*` | 3 **new** |
| `main/boards/mickey/phoe_lone_behavior.*` | 4 **new** |
| `main/application.cc` | 2 ping/pong |
| `main/protocols/websocket_protocol.cc` | 2 last-incoming vs opcode ping; 5 idle connect |
| `docs/websocket.md` | 2 ping/pong; 5 idle WS |
| `main/boards/mickey/config.json` | keep HTTPS OTA URL; `type: mickey` |

Servo patches to copy from the VPS:

```bash
# from phoelone repo root; retarget otto-robot → mickey if needed
patch -p1 < /path/to/phoe_lone_server/firmware/otto-robot/patches/001-oscillator-keep-hold.patch
patch -p1 < /path/to/phoe_lone_server/firmware/otto-robot/patches/002-home-force-stance.patch
patch -p1 < /path/to/phoe_lone_server/firmware/otto-robot/patches/003-stop-cooperative-home.patch
```

---

## 10. Smoke after each flash (do not skip)

1. Hello within 10 s of opening the channel.
2. Burmese STT then TTS.
3. Walk / stop (pose holds).
4. Dashboard Stop during TTS.
5. After Slice 2: no ping WARN; 4-minute local track survives.
6. After Slice 5: phone Play tab without wake word.

If audio, wake word, or display regresses, revert the last slice before continuing.
