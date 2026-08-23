from __future__ import annotations

import array
import math
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from app.observability.logging import get_logger

log = get_logger(__name__)

UPLINK_FRAME_MS = 60.0
SILERO_WINDOW_SAMPLES = 512
SILERO_WINDOW_BYTES = SILERO_WINDOW_SAMPLES * 2


def pcm16le_rms(pcm: bytes) -> float:
    """RMS energy of little-endian int16 mono PCM."""
    if not pcm:
        return 0.0
    if len(pcm) % 2 != 0:
        pcm = pcm[:-1]
    if not pcm:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return 0.0
    return math.sqrt(sum(int(x) * int(x) for x in samples) / len(samples))


@dataclass(frozen=True)
class SpeechGateConfig:
    speech_threshold: float = 0.5
    min_speech_ms: float = 180.0
    min_silence_ms: float = 800.0
    preroll_chunks: int = 3
    frame_ms: float = UPLINK_FRAME_MS
    energy_speech_rms: float = 500.0
    # Silero LSTM is cold after reset; loud speech scores ~0.07 for ~1s.
    # During warmup, high RMS may open the gate. Hangover stays Silero-only.
    warmup_ms: float = 1500.0
    warmup_energy_rms: float = 1800.0


class SpeechScorer(Protocol):
    def reset(self) -> None: ...

    def score(self, pcm: bytes) -> float: ...


class EnergyScorer:
    """Test-only scorer: high RMS counts as speech. Cannot reject fans."""

    def __init__(self, speech_rms: float = 500.0) -> None:
        self.speech_rms = speech_rms

    def reset(self) -> None:
        return None

    def score(self, pcm: bytes) -> float:
        return 1.0 if pcm16le_rms(pcm) >= self.speech_rms else 0.0


class SileroScorer:
    """Streaming Silero VAD over 512-sample windows at 16 kHz."""

    def __init__(self) -> None:
        from pysilero_vad import SileroVoiceActivityDetector

        self._vad = SileroVoiceActivityDetector()
        self._buf = bytearray()

    def reset(self) -> None:
        self._vad.reset()
        self._buf.clear()

    def clear_buffer(self) -> None:
        """Drop leftover PCM without wiping LSTM state."""
        self._buf.clear()

    def score(self, pcm: bytes) -> float:
        if pcm:
            self._buf.extend(pcm)
        max_prob = 0.0
        scored = False
        while len(self._buf) >= SILERO_WINDOW_BYTES:
            chunk = bytes(self._buf[:SILERO_WINDOW_BYTES])
            del self._buf[:SILERO_WINDOW_BYTES]
            max_prob = max(max_prob, float(self._vad(chunk)))
            scored = True
        return max_prob if scored else 0.0


class SpeechGate:
    """Attack / hangover endpointing on top of a per-frame speech probability.

    Close only after ``min_silence_ms`` of consecutive *non-speech* (Silero),
    not after a dip in RMS. That is what lets a fan keep streaming in auto
    mode on energy VAD, and what cuts users on a 480 ms pause.
    """

    def __init__(self, config: SpeechGateConfig, scorer: SpeechScorer) -> None:
        self.config = config
        self.scorer = scorer
        self.reset()

    def reset(self, *, reset_scorer: bool = True) -> None:
        cfg = self.config
        if reset_scorer:
            self.scorer.reset()
        else:
            clearer = getattr(self.scorer, "clear_buffer", None)
            if callable(clearer):
                clearer()
        self._open = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._elapsed_ms = 0.0
        self._preroll: deque[bytes] = deque(maxlen=max(0, cfg.preroll_chunks))
        self.accepted_chunks = 0
        self.dropped_chunks = 0
        self.ever_opened = False
        self.last_rms = 0.0
        self.last_prob = 0.0
        self.last_threshold = cfg.speech_threshold
        self.last_decision = "DROPPED"
        self.last_reason = "reset"
        self.noise_floor = 0.0
        self._last_log_key: tuple[str, str] | None = None

    @property
    def is_open(self) -> bool:
        return self._open

    def _log_chunk(self, *, decision: str, reason: str, emitted: int) -> None:
        self.last_decision = decision
        self.last_reason = reason
        key = (decision, reason)
        changed = key != self._last_log_key
        self._last_log_key = key
        payload = {
            "decision": decision,
            "reason": reason,
            "rms": round(self.last_rms, 1),
            "speech_prob": round(self.last_prob, 3),
            "threshold": round(self.last_threshold, 3),
            "open": self._open,
            "speech_ms": round(self._speech_ms, 1),
            "silence_ms": round(self._silence_ms, 1),
            "emitted": emitted,
            "accepted_total": self.accepted_chunks,
            "dropped_total": self.dropped_chunks,
        }
        if changed:
            log.info("speech_gate.chunk", **payload)
        else:
            log.debug("speech_gate.chunk", **payload)

    def _is_speech(self, pcm: bytes) -> tuple[bool, str]:
        """Silero decision, with a high-RMS fallback only while the LSTM warms up.

        Hangover / close stays Silero-only so a fan cannot hold the gate open.
        """
        cfg = self.config
        self.last_prob = self.scorer.score(pcm)
        silero_speech = self.last_prob >= cfg.speech_threshold
        warming = (not self._open) and self._elapsed_ms < cfg.warmup_ms
        if silero_speech:
            return True, "silero"
        if warming and self.last_rms >= cfg.warmup_energy_rms:
            return True, "warmup_energy"
        return False, "below_threshold"

    def process(self, pcm: bytes) -> list[bytes]:
        cfg = self.config
        self.last_rms = pcm16le_rms(pcm)
        self.last_threshold = cfg.speech_threshold
        if not pcm:
            self.last_prob = 0.0
            self._log_chunk(decision="DROPPED", reason="empty", emitted=0)
            return []

        frame_ms = cfg.frame_ms
        self._elapsed_ms += frame_ms
        is_speech, speech_kind = self._is_speech(pcm)

        if not self._open:
            if is_speech:
                self._speech_ms += frame_ms
                if cfg.preroll_chunks > 0:
                    self._preroll.append(pcm)
                if self._speech_ms >= cfg.min_speech_ms:
                    self._open = True
                    self.ever_opened = True
                    self._silence_ms = 0.0
                    self._speech_ms = 0.0
                    emitted = list(self._preroll) if self._preroll else [pcm]
                    self._preroll.clear()
                    self.accepted_chunks += len(emitted)
                    reason = "attack_open_warmup" if speech_kind == "warmup_energy" else "attack_open"
                    self._log_chunk(decision="ACCEPTED", reason=reason, emitted=len(emitted))
                    return emitted
                self._log_chunk(decision="DROPPED", reason="attack_wait", emitted=0)
                return []
            self._speech_ms = 0.0
            if cfg.preroll_chunks > 0:
                self._preroll.append(pcm)
            self.dropped_chunks += 1
            self._log_chunk(decision="DROPPED", reason="below_threshold", emitted=0)
            return []

        if is_speech:
            self._silence_ms = 0.0
            self.accepted_chunks += 1
            self._log_chunk(decision="ACCEPTED", reason="speech", emitted=1)
            return [pcm]

        self._silence_ms += frame_ms
        if self._silence_ms < cfg.min_silence_ms:
            self.accepted_chunks += 1
            self._log_chunk(decision="ACCEPTED", reason="hangover", emitted=1)
            return [pcm]

        self._open = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._preroll.clear()
        self._preroll.append(pcm)
        self.dropped_chunks += 1
        self._log_chunk(decision="DROPPED", reason="hangover_expired", emitted=0)
        return []


def create_speech_gate(
    *,
    backend: str,
    speech_threshold: float,
    min_speech_ms: float,
    min_silence_ms: float,
    preroll_chunks: int,
    energy_speech_rms: float,
    warmup_ms: float = 1500.0,
    warmup_energy_rms: float = 1800.0,
) -> SpeechGate:
    config = SpeechGateConfig(
        speech_threshold=speech_threshold,
        min_speech_ms=min_speech_ms,
        min_silence_ms=min_silence_ms,
        preroll_chunks=preroll_chunks,
        energy_speech_rms=energy_speech_rms,
        warmup_ms=warmup_ms,
        warmup_energy_rms=warmup_energy_rms,
    )
    if backend == "energy":
        scorer: SpeechScorer = EnergyScorer(energy_speech_rms)
    else:
        scorer = SileroScorer()
    return SpeechGate(config, scorer)
