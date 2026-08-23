from __future__ import annotations

from app.audio.speech_gate import SileroScorer, SpeechGate, SpeechGateConfig
from tests.fakes import _silence_pcm, _static_pcm, _tone_pcm


def test_silero_rejects_silence_and_fan_static() -> None:
    gate = SpeechGate(
        SpeechGateConfig(
            speech_threshold=0.5,
            min_speech_ms=180.0,
            min_silence_ms=800.0,
            preroll_chunks=0,
        ),
        SileroScorer(),
    )
    for _ in range(20):
        assert gate.process(_silence_pcm()) == []
    for _ in range(40):
        assert gate.process(_static_pcm(amplitude=1200)) == []
    assert gate.ever_opened is False


def test_silero_square_wave_is_not_speech() -> None:
    """Synthetic test tones are energy, not speech — integration tests use energy VAD."""
    scorer = SileroScorer()
    max_prob = 0.0
    for _ in range(10):
        max_prob = max(max_prob, scorer.score(_tone_pcm(amplitude=8000)))
    assert max_prob < 0.5
