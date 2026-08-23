from __future__ import annotations

"""
Hardware smoke (run against a live Phoe Lone on the LAN).

  CONFIG_OTA_URL=http://<vps>:8000/xiaozhi/ota/
  .venv/bin/python scripts/fake_device.py --ota http://127.0.0.1:8000/xiaozhi/ota/

Then on the robot:
  1. OTA succeeds and WebSocket hello completes
  2. Burmese utterance produces STT + TTS
  3. Walk / stop MCP tools work
"""

import os

import pytest


@pytest.mark.skipif(not os.getenv("PHOE_LONE_HARDWARE"), reason="hardware smoke is opt-in")
def test_hardware_placeholder() -> None:
    assert os.getenv("PHOE_LONE_HARDWARE")
