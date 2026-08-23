from __future__ import annotations

from app.audio.opus import DOWNLINK_FRAME_SAMPLES, LibOpusCodec, UPLINK_FRAME_SAMPLES


def test_libopus_encode_and_decode() -> None:
    codec = LibOpusCodec()
    down = codec.encode_downlink(b"\x00\x00" * DOWNLINK_FRAME_SAMPLES)
    assert isinstance(down, (bytes, bytearray)) and len(down) > 0
    # Uplink decoder is 16 kHz; feed a freshly encoded 16 kHz silence frame.
    import opuslib_next as opuslib

    enc = opuslib.Encoder(16000, 1, opuslib.APPLICATION_AUDIO)
    packet = enc.encode(b"\x00\x00" * UPLINK_FRAME_SAMPLES, UPLINK_FRAME_SAMPLES)
    pcm = codec.decode_uplink(packet)
    assert len(pcm) == UPLINK_FRAME_SAMPLES * 2
    codec.reset()
