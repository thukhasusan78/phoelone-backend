#!/usr/bin/env python3
"""Fake ESP32 client for protocol smoke tests (no hardware required)."""

from __future__ import annotations

import argparse
import json
import uuid

import httpx
from websockets.sync.client import connect


HELLO = {
    "type": "hello",
    "version": 1,
    "features": {"mcp": True},
    "transport": "websocket",
    "audio_params": {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ota", default="http://127.0.0.1:8000/xiaozhi/ota/")
    parser.add_argument("--device-id", default="aa:bb:cc:dd:ee:ff")
    parser.add_argument("--client-id", default=str(uuid.uuid4()))
    args = parser.parse_args()
    headers = {"Device-Id": args.device_id, "Client-Id": args.client_id}
    response = httpx.get(args.ota, headers=headers, timeout=10)
    response.raise_for_status()
    body = response.json()
    ws_url = body["websocket"]["url"]
    token = body["websocket"]["token"]
    print("ota websocket", ws_url)
    extra = {
        "Authorization": f"Bearer {token}",
        "Protocol-Version": "1",
        "Device-Id": args.device_id,
        "Client-Id": args.client_id,
    }
    with connect(ws_url, additional_headers=extra) as ws:
        ws.send(json.dumps(HELLO))
        hello = json.loads(ws.recv())
        print("hello", hello)
        assert hello["type"] == "hello"
        assert hello["transport"] == "websocket"
        print("ok")


if __name__ == "__main__":
    main()
