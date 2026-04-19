import logging

import numpy as np

from server import config

logger = logging.getLogger(__name__)


def pcm16_bytes_to_float32(pcm_bytes: bytes) -> np.ndarray:
    pcm16 = np.frombuffer(pcm_bytes, dtype=np.int16)
    audio = pcm16.astype(np.float32) / 32768.0
    logger.debug(
        "PCM16 -> float32: %d bytes -> %d samples",
        len(pcm_bytes),
        len(audio),
    )
    return audio


def float32_to_pcm16_bytes(audio: np.ndarray) -> bytes:
    pcm16 = (audio * 32767).clip(-32768, 32767).astype(np.int16)
    pcm_bytes = pcm16.tobytes()
    logger.debug(
        "float32 -> PCM16: %d samples -> %d bytes",
        len(audio),
        len(pcm_bytes),
    )
    return pcm_bytes


def resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        logger.debug("Resample skipped: source_rate == target_rate == %d", source_rate)
        return audio
    duration = len(audio) / source_rate
    target_length = int(duration * target_rate)
    indices = np.linspace(0, len(audio) - 1, target_length)
    resampled = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
    logger.debug(
        "Resampled audio: %d samples @ %d Hz -> %d samples @ %d Hz",
        len(audio),
        source_rate,
        len(resampled),
        target_rate,
    )
    return resampled


def ensure_mono(audio: np.ndarray) -> np.ndarray:
    if audio.ndim > 1:
        mono = audio.mean(axis=1)
        logger.debug("Downmixed audio to mono: shape %s -> %d samples", audio.shape, len(mono))
        return mono
    logger.debug("Audio already mono: %d samples", len(audio))
    return audio


def prepare_for_stt(pcm_bytes: bytes, source_rate: int) -> np.ndarray:
    audio = pcm16_bytes_to_float32(pcm_bytes)
    audio = ensure_mono(audio)
    audio = resample(audio, source_rate, config.STT_SAMPLE_RATE)
    logger.debug(
        "Prepared audio for STT: %d input bytes @ %d Hz -> %d samples @ %d Hz",
        len(pcm_bytes),
        source_rate,
        len(audio),
        config.STT_SAMPLE_RATE,
    )
    return audio
