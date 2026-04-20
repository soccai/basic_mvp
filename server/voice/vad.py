import logging

import numpy as np

from server import config

logger = logging.getLogger(__name__)


class SimpleVAD:
    def __init__(
        self,
        energy_threshold: float = config.VAD_ENERGY_THRESHOLD,
        silence_duration_ms: int = config.VAD_MIN_SILENCE_MS,
        min_speech_ms: int = config.VAD_MIN_SPEECH_MS,
        sample_rate: int = config.STT_SAMPLE_RATE,
    ):
        self.energy_threshold = energy_threshold
        self.silence_duration_ms = silence_duration_ms
        self.min_speech_ms = min_speech_ms
        self.sample_rate = sample_rate
        self.silence_samples = 0
        self.speech_samples = 0       # total samples where speech was detected
        self.has_speech = False

    def process_chunk(self, audio_chunk: np.ndarray) -> str:
        rms = np.sqrt(np.mean(audio_chunk**2)) if len(audio_chunk) > 0 else 0.0
        silence_threshold_samples = (self.silence_duration_ms / 1000.0) * self.sample_rate
        min_speech_samples = (self.min_speech_ms / 1000.0) * self.sample_rate

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
            self.speech_samples += len(audio_chunk)
            return "speech"
        else:
            self.silence_samples += len(audio_chunk)
            if self.has_speech and self.silence_samples >= silence_threshold_samples:
                # Only emit speech_end if enough speech was accumulated
                if self.speech_samples >= min_speech_samples:
                    logger.debug(
                        "VAD speech end: rms=%.5f silence=%d samples, speech=%d samples",
                        rms,
                        self.silence_samples,
                        self.speech_samples,
                    )
                    self.has_speech = False
                    self.silence_samples = 0
                    self.speech_samples = 0
                    return "speech_end"
                else:
                    # Too short — discard as noise/fragment
                    logger.debug(
                        "VAD discard fragment: speech=%d samples < min=%d",
                        self.speech_samples,
                        min_speech_samples,
                    )
                    self.has_speech = False
                    self.silence_samples = 0
                    self.speech_samples = 0
                    return "discard"
            return "silence"

    def reset(self):
        self.silence_samples = 0
        self.speech_samples = 0
        self.has_speech = False
        logger.debug("VAD state reset")
