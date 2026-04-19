import logging

import numpy as np

from server import config

logger = logging.getLogger(__name__)


class SimpleVAD:
    def __init__(
        self,
        energy_threshold: float = config.VAD_ENERGY_THRESHOLD,
        silence_duration_ms: int = config.VAD_MIN_SILENCE_MS,
        sample_rate: int = config.STT_SAMPLE_RATE,
    ):
        self.energy_threshold = energy_threshold
        self.silence_duration_ms = silence_duration_ms
        self.sample_rate = sample_rate
        self.silence_samples = 0
        self.has_speech = False

    def process_chunk(self, audio_chunk: np.ndarray) -> str:
        rms = np.sqrt(np.mean(audio_chunk**2)) if len(audio_chunk) > 0 else 0.0
        silence_threshold_samples = (self.silence_duration_ms / 1000.0) * self.sample_rate

        if rms > self.energy_threshold:
            if not self.has_speech:
                logger.debug(
                    "VAD speech start: rms=%.5f threshold=%.5f chunk=%d samples",
                    rms,
                    self.energy_threshold,
                    len(audio_chunk),
                )
            self.has_speech = True
            self.silence_samples = 0
            return "speech"
        else:
            self.silence_samples += len(audio_chunk)
            if self.has_speech and self.silence_samples >= silence_threshold_samples:
                self.has_speech = False
                self.silence_samples = 0
                logger.debug(
                    "VAD speech end: rms=%.5f silence_threshold=%d samples",
                    rms,
                    silence_threshold_samples,
                )
                return "speech_end"
            return "silence"

    def reset(self):
        self.silence_samples = 0
        self.has_speech = False
        logger.debug("VAD state reset")
