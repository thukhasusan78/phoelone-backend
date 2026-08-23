from __future__ import annotations

import struct

from app.audio.speech_gate import (
    EnergyScorer,
    SpeechGate,
    SpeechGateConfig,
    create_speech_gate,
    pcm16le_rms,
)
from tests.fakes import _silence_pcm, _static_pcm, _tone_pcm


def _energy_gate(**overrides) -> SpeechGate:
    params = {
        "speech_threshold": 0.5,
        "min_speech_ms": 180.0,
        "min_silence_ms": 800.0,
        "preroll_chunks": 3,
        "energy_speech_rms": 500.0,
    }
    params.update(overrides)
    cfg = SpeechGateConfig(**params)
    return SpeechGate(cfg, EnergyScorer(cfg.energy_speech_rms))


def test_pcm16le_rms_silence_and_tone() -> None:
    assert pcm16le_rms(_silence_pcm()) == 0.0
    assert pcm16le_rms(_tone_pcm(amplitude=4000)) > 3000


def test_quiet_chunks_are_dropped() -> None:
    gate = _energy_gate(energy_speech_rms=500.0)
    forwarded = 0
    for _ in range(50):
        forwarded += len(gate.process(_static_pcm(amplitude=80)))
    assert forwarded == 0
    assert gate.ever_opened is False
    assert gate.dropped_chunks == 50


def test_typical_esp32_speech_energy_opens() -> None:
    gate = _energy_gate(min_speech_ms=60.0, preroll_chunks=0, energy_speech_rms=500.0)
    forwarded = 0
    for _ in range(8):
        forwarded += len(gate.process(_tone_pcm(amplitude=1600)))
    assert gate.ever_opened is True
    assert forwarded > 0


def test_speech_opens_with_preroll() -> None:
    gate = _energy_gate(
        min_speech_ms=180.0,
        preroll_chunks=3,
        energy_speech_rms=500.0,
    )
    for _ in range(5):
        assert gate.process(_static_pcm(amplitude=80)) == []

    for _ in range(2):
        assert gate.process(_tone_pcm(amplitude=8000)) == []
    opened = gate.process(_tone_pcm(amplitude=8000))
    assert len(opened) >= 2
    assert gate.ever_opened is True
    assert gate.is_open is True


def test_short_pause_keeps_hangover_open() -> None:
    gate = _energy_gate(
        min_speech_ms=60.0,
        min_silence_ms=800.0,
        preroll_chunks=0,
        energy_speech_rms=200.0,
    )
    assert gate.process(_tone_pcm(amplitude=8000))
    # 400 ms pause (6-7 frames) must not endpoint at 800 ms hangover.
    for _ in range(7):
        out = gate.process(_static_pcm(amplitude=80))
        assert out, "natural pause should stay inside 800ms hangover"
    assert gate.is_open is True


def test_800ms_non_speech_expires_hangover() -> None:
    gate = _energy_gate(
        min_speech_ms=60.0,
        min_silence_ms=800.0,
        preroll_chunks=0,
        energy_speech_rms=200.0,
    )
    assert gate.process(_tone_pcm(amplitude=8000))
    closed = False
    for _ in range(20):
        out = gate.process(_static_pcm(amplitude=80))
        if not out:
            closed = True
            assert gate.last_reason == "hangover_expired"
            break
    assert closed
    assert gate.is_open is False


def test_trailing_noise_after_close_is_dropped() -> None:
    gate = _energy_gate(
        min_speech_ms=60.0,
        min_silence_ms=60.0,
        preroll_chunks=0,
        energy_speech_rms=200.0,
    )
    assert gate.process(_tone_pcm(amplitude=8000))
    assert gate.process(_static_pcm(amplitude=50)) == []  # expired
    assert gate.process(_static_pcm(amplitude=80)) == []


def test_energy_backend_treats_fan_static_as_speech() -> None:
    """Documents why production must not use energy VAD around a fan."""
    gate = create_speech_gate(
        backend="energy",
        speech_threshold=0.5,
        min_speech_ms=60.0,
        min_silence_ms=800.0,
        preroll_chunks=0,
        energy_speech_rms=500.0,
    )
    assert gate.process(_static_pcm(amplitude=1200))
    assert gate.ever_opened is True


def test_reset_clears_state() -> None:
    gate = _energy_gate(min_speech_ms=60.0, preroll_chunks=0)
    gate.process(_tone_pcm(amplitude=8000))
    assert gate.ever_opened
    gate.reset()
    assert gate.ever_opened is False
    assert gate.accepted_chunks == 0
    assert gate.dropped_chunks == 0
    assert gate.is_open is False


def test_odd_byte_pcm_handled() -> None:
    pcm = struct.pack("<hhh", 1000, -1000, 1000) + b"\x01"
    assert pcm16le_rms(pcm) > 0


class _ColdSilero:
    """Mimics Silero LSTM warmup: loud speech still scores ~0.07 at first."""

    def reset(self) -> None:
        return None

    def score(self, pcm: bytes) -> float:
        return 0.07


def test_warmup_energy_opens_while_silero_is_cold() -> None:
    gate = SpeechGate(
        SpeechGateConfig(
            speech_threshold=0.5,
            min_speech_ms=180.0,
            min_silence_ms=800.0,
            preroll_chunks=3,
            warmup_ms=1500.0,
            warmup_energy_rms=1800.0,
        ),
        _ColdSilero(),
    )
    opened = b""
    for _ in range(5):
        opened = gate.process(_tone_pcm(amplitude=4000))
        if opened:
            break
    assert opened
    assert gate.ever_opened is True
    assert gate.last_reason == "attack_open_warmup"


def test_warmup_does_not_open_on_fan_static() -> None:
    gate = SpeechGate(
        SpeechGateConfig(
            speech_threshold=0.5,
            min_speech_ms=60.0,
            min_silence_ms=800.0,
            preroll_chunks=0,
            warmup_ms=1500.0,
            warmup_energy_rms=1800.0,
        ),
        _ColdSilero(),
    )
    for _ in range(20):
        assert gate.process(_static_pcm(amplitude=1200)) == []
    assert gate.ever_opened is False


def test_hangover_ignores_warmup_energy() -> None:
    """Once open, only Silero (not RMS) may keep the gate open."""

    class _SilentAfterOpen:
        def __init__(self) -> None:
            self.n = 0

        def reset(self) -> None:
            return None

        def score(self, pcm: bytes) -> float:
            self.n += 1
            return 0.9 if self.n == 1 else 0.01

    gate = SpeechGate(
        SpeechGateConfig(
            speech_threshold=0.5,
            min_speech_ms=60.0,
            min_silence_ms=60.0,
            preroll_chunks=0,
            warmup_ms=5000.0,
            warmup_energy_rms=500.0,
        ),
        _SilentAfterOpen(),
    )
    assert gate.process(_tone_pcm(amplitude=4000))
    # High RMS but Silero now low — hangover should expire, not stay open via energy.
    assert gate.process(_tone_pcm(amplitude=4000)) == []
    assert gate.last_reason == "hangover_expired"
