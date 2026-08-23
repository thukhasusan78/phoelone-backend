from __future__ import annotations

from app.audio.opus import DOWNLINK_FRAME_SAMPLES, iter_pcm_frames
from app.tools.http import HttpGuardError, assert_public_https
import pytest


def test_iter_pcm_pads_last_frame() -> None:
    pcm = b"\x01\x02" * 10
    frames = iter_pcm_frames(pcm)
    assert frames
    assert all(len(f) == DOWNLINK_FRAME_SAMPLES * 2 for f in frames)


def test_http_guard_blocks_private() -> None:
    with pytest.raises(HttpGuardError):
        assert_public_https("http://example.com")
    with pytest.raises(HttpGuardError):
        assert_public_https("https://127.0.0.1/x")
    with pytest.raises(HttpGuardError):
        assert_public_https("https://169.254.169.254/latest")
    assert_public_https("https://api.open-meteo.com/v1/forecast")
