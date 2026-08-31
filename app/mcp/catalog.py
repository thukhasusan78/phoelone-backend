"""English MCP catalog for Mickey (OTA board type ``mickey``) LLM-visible tools.

Device tools are implemented on the ESP32. This module supplies complete
English descriptions and input schemas so Gemini can call them accurately.
User-only companion tools are never exposed to the LLM.
"""

from __future__ import annotations

from typing import Any

USER_ONLY_TOOLS = frozenset(
    {
        "self.get_system_info",
        "self.reboot",
        "self.upgrade_firmware",
        "self.screen.get_info",
        "self.screen.snapshot",
        "self.screen.preview_image",
        "self.assets.set_download_url",
    }
)

FORBIDDEN_BOARDS = ("self.chassis.", "self.dog.", "self.electron.")

OTTO_ACTIONS = [
    "walk",
    "turn",
    "jump",
    "swing",
    "moonwalk",
    "bend",
    "shake_leg",
    "updown",
    "whirlwind_leg",
    "sit",
    "showcase",
    "home",
    "hands_up",
    "hands_down",
    "hand_wave",
    "windmill",
    "takeoff",
    "fitness",
    "greeting",
    "shy",
    "radio_calisthenics",
    "magic_circle",
]


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


LLM_TOOLS: dict[str, dict[str, Any]] = {
    "self.get_device_status": {
        "name": "self.get_device_status",
        "description": (
            "Read robot status: speaker volume, screen brightness/theme, battery, "
            "Wi-Fi, and chip temperature. Use for volume, battery, or network questions."
        ),
        "inputSchema": _object({}),
    },
    "self.audio_speaker.set_volume": {
        "name": "self.audio_speaker.set_volume",
        "description": "Set speaker volume. volume is required, integer 0-100.",
        "inputSchema": _object(
            {"volume": {"type": "integer", "description": "Volume 0-100"}},
            ["volume"],
        ),
    },
    "self.screen.set_brightness": {
        "name": "self.screen.set_brightness",
        "description": "Set LCD backlight brightness. brightness is required, integer 0-100.",
        "inputSchema": _object(
            {"brightness": {"type": "integer", "description": "Brightness 0-100"}},
            ["brightness"],
        ),
    },
    "self.screen.set_theme": {
        "name": "self.screen.set_theme",
        "description": "Set display theme to light or dark.",
        "inputSchema": _object(
            {"theme": {"type": "string", "enum": ["light", "dark"], "description": "Theme name"}},
            ["theme"],
        ),
    },
    "self.otto.action": {
        "name": "self.otto.action",
        "description": (
            "Move, dance, or pose. action is required. "
            "walk/turn use direction 1=forward/left, -1=back/right, 0=both. "
            "speed is milliseconds-style: smaller is faster (100-3000). "
            "First walk/turn: steps=2, speed=2000. "
            "Do not use this tool to stop; call self.otto.stop. "
            "Hand actions (hands_up, hands_down, hand_wave, windmill, takeoff, fitness, "
            "greeting, shy, radio_calisthenics, magic_circle) fail on this 4-servo build."
        ),
        "inputSchema": _object(
            {
                "action": {
                    "type": "string",
                    "enum": OTTO_ACTIONS,
                    "description": "Motion name",
                },
                "steps": {"type": "integer", "description": "Repeats 1-100, default 3"},
                "speed": {
                    "type": "integer",
                    "description": "Duration-style speed 100-3000; smaller is faster; default 700",
                },
                "direction": {
                    "type": "integer",
                    "description": "1=forward/left, -1=back/right, 0=both",
                },
                "amount": {"type": "integer", "description": "Amplitude 0-170"},
                "arm_swing": {"type": "integer", "description": "Arm swing 0-170 for walk/turn"},
            },
            ["action"],
        ),
    },
    "self.otto.stop": {
        "name": "self.otto.stop",
        "description": (
            "Immediately stop motion, clear the action queue, and home servos. "
            "Call ONLY when the user explicitly says stop. "
            "Never call on silence, filler, or unintelligible audio."
        ),
        "inputSchema": _object({}),
    },
    "self.otto.servo_sequences": {
        "name": "self.otto.servo_sequences",
        "description": (
            "Run a short custom servo sequence. sequence must be a JSON STRING, not an object. "
            "Format: {\"a\":[{\"s\":{\"ll\":90,\"rl\":90,\"lf\":90,\"rf\":90},\"v\":1000,\"d\":0}],\"d\":0}. "
            "Servo keys: ll left leg, rl right leg, lf left foot, rf right foot, lh left hand, rh right hand. "
            "Degrees 0-180. When oscillating legs/feet, one foot must stay at 90. "
            "Queue holds 10 items; keep sequences short. Then call self.otto.action home or self.otto.stop."
        ),
        "inputSchema": _object(
            {
                "sequence": {
                    "type": "string",
                    "description": "JSON string of the servo sequence",
                }
            },
            ["sequence"],
        ),
    },
    "self.otto.set_trim": {
        "name": "self.otto.set_trim",
        "description": "Calibrate one servo trim (-50 to 50) and preview with a small jump.",
        "inputSchema": _object(
            {
                "servo_type": {
                    "type": "string",
                    "enum": [
                        "left_leg",
                        "right_leg",
                        "left_foot",
                        "right_foot",
                        "left_hand",
                        "right_hand",
                    ],
                },
                "trim_value": {"type": "integer", "description": "Trim -50 to 50"},
            },
            ["servo_type", "trim_value"],
        ),
    },
    "self.otto.get_trims": {
        "name": "self.otto.get_trims",
        "description": "Return persisted servo trim values as JSON.",
        "inputSchema": _object({}),
    },
    "self.otto.get_status": {
        "name": "self.otto.get_status",
        "description": "Return whether the robot is moving or idle.",
        "inputSchema": _object({}),
    },
    "self.battery.get_level": {
        "name": "self.battery.get_level",
        "description": "Return battery level percent and charging flag.",
        "inputSchema": _object({}),
    },
    "self.otto.get_ip": {
        "name": "self.otto.get_ip",
        "description": "Return local IP address and Wi-Fi connected flag.",
        "inputSchema": _object({}),
    },
    "self.phoe_lone.imu.get_reading": {
        "name": "self.phoe_lone.imu.get_reading",
        "description": (
            "Read the MPU6050 IMU. Returns wired:true with ax/ay/az, gx/gy/gz, pitch, roll, "
            "temp_c, and event (still|moving|pickup|putdown|fall|shake) when the sensor is live; "
            "wired:false on stub firmware. If wired:false or ok:false, say the sensor is not "
            "connected or failed — never invent accelerometer or gyro values. "
            "Use for 'are you being held' or 'did you fall'. If event is fall, call "
            "self.otto.stop if moving; do not walk."
        ),
        "inputSchema": _object({}),
    },
    "self.phoe_lone.light.get_level": {
        "name": "self.phoe_lone.light.get_level",
        "description": (
            "Read the light sensor. Returns wired:true with lux, bucket "
            "(dark|dim|indoor|bright), and raw when live; wired:false on stub firmware. "
            "If wired:false or ok:false, say the sensor is not connected — never invent a lux "
            "reading. Call this when the user asks if it is dark or bright."
        ),
        "inputSchema": _object({}),
    },
    "self.phoe_lone.touch.get_state": {
        "name": "self.phoe_lone.touch.get_state",
        "description": (
            "Read the head touch sensor. Returns wired:true with touched, count, and ms_held "
            "when live; wired:false on stub firmware. A pet may also arrive as an MCP "
            "notification — do not require the user to say they petted you. "
            "If wired:false or ok:false, say the sensor is not connected. "
            "Never invent touch state."
        ),
        "inputSchema": _object({}),
    },
    "self.mickey.alarm.set": {
        "name": "self.mickey.alarm.set",
        "description": (
            "Set the overnight wake alarm on the robot clock (local time, 24-hour). "
            "hour is 0-23, minute is 0-59. 7:00 AM is hour=7, minute=0; 7:00 PM is hour=19. "
            "repeat=true means every day; repeat=false means once. "
            "Firmware defaults omitted repeat to daily — always pass repeat explicitly. "
            "sleep_now=true also starts deep sleep after storing the alarm "
            "(good night + wake-me-at). Do not invent a time; ask if the hour is missing."
        ),
        "inputSchema": _object(
            {
                "hour": {"type": "integer", "description": "Local hour 0-23"},
                "minute": {"type": "integer", "description": "Local minute 0-59"},
                "repeat": {
                    "type": "boolean",
                    "description": "true=daily (firmware default if omitted), false=once",
                },
                "sleep_now": {
                    "type": "boolean",
                    "description": "true=enter deep sleep after saving the alarm",
                },
            },
            ["hour", "minute"],
        ),
    },
    "self.mickey.alarm.get": {
        "name": "self.mickey.alarm.get",
        "description": (
            "Read the stored wake alarm. Call when the user asks what time the alarm is, "
            "whether an alarm is set, or to confirm before changing it."
        ),
        "inputSchema": _object({}),
    },
    "self.mickey.alarm.cancel": {
        "name": "self.mickey.alarm.cancel",
        "description": (
            "Clear the stored wake alarm. Call when the user cancels the alarm, "
            "says do not wake them, or turns the alarm off."
        ),
        "inputSchema": _object({}),
    },
    "self.mickey.sleep.now": {
        "name": "self.mickey.sleep.now",
        "description": (
            "Enter deep sleep until a wake time. "
            "With no args: uses the stored enabled alarm; fails with "
            "'no wake time; set hour/minute or seconds' if none is set. "
            "Pass hour (0-23) and minute (0-59) to override or store a wake time. "
            "Pass seconds (1-86400) for a bench-test timer that does not need a synced clock. "
            "Good night with a wake time: prefer self.mickey.alarm.set with sleep_now=true. "
            "Good night with no wake time: call this empty only if an alarm is already stored, "
            "or pass seconds for a nap. Not a chat-exit tool."
        ),
        "inputSchema": _object(
            {
                "hour": {
                    "type": "integer",
                    "description": "Optional local hour 0-23; omit to use the stored alarm",
                },
                "minute": {
                    "type": "integer",
                    "description": "Optional local minute 0-59",
                },
                "seconds": {
                    "type": "integer",
                    "description": "Optional 1-86400 bench-test sleep seconds; no synced clock required",
                },
            }
        ),
    },
    "self.music.play_song": {
        "name": "self.music.play_song",
        "description": (
            "Play a song on firmware that includes a music player. "
            "song_name is required. Optional artist_name."
        ),
        "inputSchema": _object(
            {
                "song_name": {"type": "string", "description": "Song title"},
                "artist_name": {"type": "string", "description": "Artist name"},
            },
            ["song_name"],
        ),
    },
    "self.online_music.play_music": {
        "name": "self.online_music.play_music",
        "description": "Stream a song from an HTTPS audio URL on the device speaker.",
        "inputSchema": _object(
            {
                "url": {"type": "string", "description": "Playable audio URL"},
                "song_name": {"type": "string", "description": "Song title"},
                "artist": {"type": "string", "description": "Artist name"},
            },
            ["url"],
        ),
    },
}

PHOE_LONE_FALLBACK_NAMES = [
    "self.get_device_status",
    "self.audio_speaker.set_volume",
    "self.screen.set_brightness",
    "self.screen.set_theme",
    "self.otto.action",
    "self.otto.stop",
    "self.otto.servo_sequences",
    "self.otto.set_trim",
    "self.otto.get_trims",
    "self.otto.get_status",
    "self.battery.get_level",
    "self.otto.get_ip",
    "self.mickey.alarm.set",
    "self.mickey.alarm.get",
    "self.mickey.alarm.cancel",
    "self.mickey.sleep.now",
]

MICKEY_DEVICE_TOOLS = (
    "self.mickey.alarm.set",
    "self.mickey.alarm.get",
    "self.mickey.alarm.cancel",
    "self.mickey.sleep.now",
)

# Only expose to Gemini when tools/list returned them (Mickey I2C is often NC).
PHOE_LONE_SENSOR_TOOLS = (
    "self.phoe_lone.imu.get_reading",
    "self.phoe_lone.light.get_level",
    "self.phoe_lone.touch.get_state",
)


def is_forbidden(name: str) -> bool:
    return name in USER_ONLY_TOOLS or any(name.startswith(p) for p in FORBIDDEN_BOARDS)


def catalog_entry(name: str) -> dict[str, Any] | None:
    entry = LLM_TOOLS.get(name)
    return dict(entry) if entry else None
