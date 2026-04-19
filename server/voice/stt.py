import logging
import numpy as np
from server import config

logger = logging.getLogger(__name__)


class STTAdapter:
    def __init__(self):
        self.model = None
        self.ready: bool = False

    def load(self) -> bool:
        try:
            from faster_whisper import WhisperModel

            logger.debug(
                "Loading STT model: model=%s device=%s compute_type=%s",
                config.WHISPER_MODEL_SIZE,
                config.WHISPER_DEVICE,
                config.WHISPER_COMPUTE_TYPE,
            )
            self.model = WhisperModel(
                config.WHISPER_MODEL_SIZE,
                device=config.WHISPER_DEVICE,
                compute_type=config.WHISPER_COMPUTE_TYPE,
            )
            self.ready = True
            logger.info("STT model loaded: %s", config.WHISPER_MODEL_SIZE)
            return True
        except Exception as e:
            logger.error("STT model load failed: %s", e)
            self.ready = False
            return False

    def transcribe(self, audio: np.ndarray) -> str:
        if not self.ready or self.model is None:
            logger.debug("STT transcribe skipped: model not ready")
            return ""
        if len(audio) == 0:
            logger.debug("STT transcribe skipped: empty audio")
            return ""
        try:
            logger.debug("STT transcribe starting: %d samples", len(audio))
            segments, _ = self.model.transcribe(
                audio,
                beam_size=config.WHISPER_BEAM_SIZE,
                language="en",
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=config.VAD_MIN_SILENCE_MS,
                ),
            )
            transcript = " ".join(seg.text for seg in segments).strip()
            logger.debug("STT transcribe completed: %d chars", len(transcript))
            return transcript
        except Exception as e:
            logger.error("STT transcribe failed: %s", e)
            return ""
