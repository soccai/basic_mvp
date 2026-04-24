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
            segments, info = self.model.transcribe(
                audio,
                beam_size=config.WHISPER_BEAM_SIZE,
                language="en",
                vad_filter=True,
                condition_on_previous_text=False,
            )

            segment_list = list(segments)
            transcript = " ".join(seg.text for seg in segment_list).strip()

            if not transcript:
                logger.debug("STT produced empty transcript")
                return ""

            no_speech_prob = getattr(info, "no_speech_prob", None)
            if no_speech_prob is None and segment_list:
                probs = [
                    getattr(seg, "no_speech_prob", None)
                    for seg in segment_list
                    if getattr(seg, "no_speech_prob", None) is not None
                ]
                if probs:
                    no_speech_prob = float(sum(probs) / len(probs))

            cleaned = " ".join(transcript.lower().split())
            word_count = len(cleaned.split())

            # Guard against common Whisper hallucinations on near-silence/noise.
            if no_speech_prob is not None and no_speech_prob > 0.60 and word_count <= 8:
                logger.info(
                    "STT transcript dropped as likely noise (no_speech_prob=%.3f, words=%d)",
                    no_speech_prob,
                    word_count,
                )
                return ""

            logger.debug("STT transcribe completed: %d chars", len(transcript))
            return transcript
        except Exception as e:
            logger.error("STT transcribe failed: %s", e)
            return ""
