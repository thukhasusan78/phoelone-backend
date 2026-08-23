# Otto firmware patches (servo hold after stop)

These patches fix servo sag / torque loss after `self.otto.stop` in the
xiaozhi-esp32 `otto-robot` board. Apply them in your firmware tree under
`main/boards/otto-robot/`.

## Root cause

1. `self.otto.stop` calls `vTaskDelete`, then starts a new `ActionTask`.
2. `ActionTask` always calls `AttachServos()` → `Oscillator::Attach()`.
3. `Attach()` called `Detach()` / `ledc_stop` (PWM off = torque released) and
   left duty at 0 (`Write(pos_)` was commented out).
4. `Otto::Home()` skipped the move when `is_otto_resting_ == true`, so PWM
   could stay off after a spurious stop while already standing.

## Apply

From the xiaozhi-esp32 repo root:

```bash
patch -p1 < /path/to/phoe_lone_server/firmware/otto-robot/patches/001-oscillator-keep-hold.patch
patch -p1 < /path/to/phoe_lone_server/firmware/otto-robot/patches/002-home-force-stance.patch
patch -p1 < /path/to/phoe_lone_server/firmware/otto-robot/patches/003-stop-cooperative-home.patch
```

Rebuild and flash the otto-robot firmware profile.

## What changed

| File | Change |
|------|--------|
| `oscillator.cc` | If already attached, refresh `Write(pos_)` and return (no `ledc_stop`). After first attach, immediately `Write(pos_)`. |
| `otto_movements.cc` | `Home()` always re-applies 90° (and hand homes); do not skip when resting. |
| `otto_controller.cc` | Cooperative stop: set `stop_requested_`, clear queue, `SetRestState(false)`, queue `ACTION_HOME` without `vTaskDelete`. Action loops abort early when stop is requested. |

Trims in NVS are unchanged; stop does not reset calibration.
