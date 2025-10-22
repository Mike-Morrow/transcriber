from __future__ import annotations

import wave
from typing import Tuple

import numpy as np


def seconds_to_frames(seconds: float, sample_rate: int) -> int:
	return max(0, int(round(seconds * sample_rate)))


def splice_audio(
	base_audio: np.ndarray,
	sample_rate: int,
	start_sec: float,
	end_sec: float,
	insert_audio: np.ndarray,
) -> np.ndarray:
	"""Replace the time range [start_sec, end_sec) in base_audio with insert_audio.

	Returns a new numpy array.
	"""
	if base_audio.ndim == 1:
		base_audio = base_audio[:, None]
	if insert_audio.ndim == 1:
		insert_audio = insert_audio[:, None]

	start_idx = seconds_to_frames(start_sec, sample_rate)
	end_idx = seconds_to_frames(end_sec, sample_rate)

	start_idx = min(max(start_idx, 0), len(base_audio))
	end_idx = min(max(end_idx, start_idx), len(base_audio))

	prefix = base_audio[:start_idx]
	suffix = base_audio[end_idx:]
	combined = np.concatenate([prefix, insert_audio, suffix], axis=0)
	return combined.astype(np.float32)


def save_wav(file_path: str, audio: np.ndarray, sample_rate: int, channels: int) -> None:
	if audio.ndim == 1 and channels > 1:
		# Broadcast mono to multi-channel if needed
		audio = np.repeat(audio[:, None], channels, axis=1)
	elif audio.ndim == 2 and audio.shape[1] != channels:
		raise ValueError("Channel mismatch between audio data and requested channels")

	audio = np.clip(audio, -1.0, 1.0)
	pcm16 = (audio * 32767.0).astype(np.int16)
	with wave.open(file_path, "wb") as wf:
		wf.setnchannels(channels)
		wf.setsampwidth(2)
		wf.setframerate(sample_rate)
		wf.writeframes(pcm16.tobytes())


def load_wav(file_path: str) -> Tuple[np.ndarray, int, int]:
	with wave.open(file_path, "rb") as wf:
		channels = wf.getnchannels()
		sample_rate = wf.getframerate()
		nframes = wf.getnframes()
		pcm_bytes = wf.readframes(nframes)
	audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32767.0
	if channels > 1:
		audio = audio.reshape(-1, channels)
	return audio, sample_rate, channels
