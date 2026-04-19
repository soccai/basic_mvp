import io
import logging
import subprocess
import wave

from server import config

logger = logging.getLogger(__name__)


def _make_wav(pcm_data: bytes, sample_rate: int = config.PIPER_SAMPLE_RATE, channels: int = 1, sample_width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    wav_bytes = buf.getvalue()
    logger.debug(
        "Wrapped raw PCM in WAV: %d PCM bytes -> %d WAV bytes",
        len(pcm_data),
        len(wav_bytes),
    )
    return wav_bytes


class TTSAdapter:
    def __init__(self):
        self.ready: bool = False
        self._backend: str = "none"
        self._piper_voice = None

    def load(self) -> bool:
        logger.debug("Loading TTS backend")
        # Try piper-tts Python package first
        if self._try_piper_python():
            return True
        # Try piper CLI
        if self._try_piper_cli():
            return True
        logger.warning("No TTS backend available. Browser speechSynthesis will be used as fallback.")
        return False

    def _try_piper_python(self) -> bool:
        try:
            from piper import PiperVoice

            model_path = str(config.PIPER_MODEL_PATH)
            config_path = str(config.PIPER_CONFIG_PATH)
            logger.debug("Trying piper Python backend with model %s", model_path)
            if not config.PIPER_MODEL_PATH.exists():
                logger.info("Piper model file not found at %s", model_path)
                return False
            self._piper_voice = PiperVoice.load(model_path, config_path=config_path)
            self._backend = "piper_python"
            self.ready = True
            logger.info("TTS loaded: piper-tts Python package")
            return True
        except Exception as e:
            logger.info("piper-tts Python package not available: %s", e)
            return False

    def _try_piper_cli(self) -> bool:
        try:
            logger.debug("Trying piper CLI backend")
            result = subprocess.run(["which", "piper"], capture_output=True, timeout=5)
            if result.returncode != 0:
                logger.debug("piper CLI not found in PATH")
                return False
            if not config.PIPER_MODEL_PATH.exists():
                logger.info("Piper CLI found but model file missing")
                return False
            self._backend = "piper_cli"
            self.ready = True
            logger.info("TTS loaded: piper CLI")
            return True
        except Exception as e:
            logger.info("piper CLI not available: %s", e)
            return False

    def synthesize(self, text: str) -> bytes:
        if not text.strip():
            logger.debug("TTS synthesize skipped: empty text")
            return b""
        if not self.ready:
            logger.debug("TTS synthesize skipped: backend not ready")
            return b""
        try:
            logger.debug("TTS synthesize starting: backend=%s chars=%d", self._backend, len(text))
            if self._backend == "piper_python":
                return self._synthesize_python(text)
            elif self._backend == "piper_cli":
                return self._synthesize_cli(text)
        except Exception as e:
            logger.error("TTS synthesis failed: %s", e)
        return b""

    def _synthesize_python(self, text: str) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            self._piper_voice.synthesize_wav(text, wf)
        wav_bytes = buf.getvalue()
        logger.debug("TTS piper_python completed: %d WAV bytes", len(wav_bytes))
        return wav_bytes

    def _synthesize_cli(self, text: str) -> bytes:
        result = subprocess.run(
            [
                "piper",
                "--model", str(config.PIPER_MODEL_PATH),
                "--output-raw",
            ],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.error("Piper CLI error: %s", result.stderr.decode())
            return b""
        # Wrap raw PCM16 in WAV header
        wav_bytes = _make_wav(result.stdout)
        logger.debug("TTS piper_cli completed: %d WAV bytes", len(wav_bytes))
        return wav_bytes
