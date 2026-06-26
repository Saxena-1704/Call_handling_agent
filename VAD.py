import numpy as np
import torch
from silero_vad import load_silero_vad, VADIterator


class VoiceActivityDetector:
    MIN_SAMPLES = 512

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_duration_ms: int = 250,
        speech_pad_ms: int = 30,
        sample_rate: int = 16000,
    ):
        self.sample_rate = sample_rate
        model = load_silero_vad()
        self._vad = VADIterator(
            model=model,
            threshold=threshold,
            sampling_rate=sample_rate,
            min_silence_duration_ms=min_silence_duration_ms,
            speech_pad_ms=speech_pad_ms,
        )
        self._buf: list[np.ndarray] = []
        self._speech: list[np.ndarray] = []
        self._triggered = False
        self._speech_just_started = False

    @property
    def is_speaking(self) -> bool:
        return self._triggered

    def process(self, chunk: np.ndarray) -> tuple[np.ndarray | None, bool]:
        self._buf.append(chunk)
        total = sum(len(c) for c in self._buf)
        if total < self.MIN_SAMPLES:
            return None, False

        full = np.concatenate(self._buf)
        vad_win = full[: self.MIN_SAMPLES]
        residual = full[self.MIN_SAMPLES :]
        self._buf = [residual] if len(residual) > 0 else []

        audio_float = vad_win.astype(np.float32) / 32768.0
        audio_tensor = torch.from_numpy(audio_float)
        event = self._vad(audio_tensor, return_seconds=False) or {}

        started = False
        if "start" in event:
            self._triggered = True
            self._speech = [vad_win]
            self._speech_just_started = True
            started = True

        elif self._triggered:
            self._speech.append(vad_win)

        if "end" in event and self._triggered:
            segment = np.concatenate(self._speech)
            self._speech.clear()
            self._triggered = False
            return segment, started

        return None, started

    @property
    def speech_just_started(self) -> bool:
        val = self._speech_just_started
        self._speech_just_started = False
        return val

    def flush(self) -> np.ndarray | None:
        if not self._speech and not self._buf:
            return None
        if self._speech:
            if self._buf:
                self._speech.extend(self._buf)
            segment = np.concatenate(self._speech)
        else:
            segment = np.concatenate(self._buf)
        self._speech.clear()
        self._buf.clear()
        self._triggered = False
        self._vad.reset_states()
        return segment

    def reset(self) -> None:
        self._buf.clear()
        self._speech.clear()
        self._triggered = False
        self._speech_just_started = False
        self._vad.reset_states()
