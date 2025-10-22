import threading
import queue
import time
import wave
from typing import Optional, Tuple

import numpy as np

try:
	import sounddevice as sd
except Exception as exc:  # pragma: no cover
	sd = None


class AudioRecorder:
	"""Simple audio recorder using sounddevice.

	Records float32 audio frames, converts to 16-bit PCM on save.
	"""

	def __init__(self, sample_rate: int = 44100, channels: int = 1) -> None:
		self.sample_rate = sample_rate
		self.channels = channels
		self._stream = None
		self._frames_q: "queue.Queue[np.ndarray]" = queue.Queue()
		self._collect_thread: Optional[threading.Thread] = None
		self._stop_event = threading.Event()
		self._buffer: Optional[np.ndarray] = None

	def is_recording(self) -> bool:
		return self._stream is not None

	def _callback(self, indata, frames, time_info, status):  # noqa: ANN001
		if status:  # pragma: no cover
			# Avoid printing inside callback in production
			pass
		# Copy to avoid referencing underlying buffer
		self._frames_q.put(indata.copy())

	def start(self) -> None:
		if sd is None:
			raise RuntimeError("sounddevice is not available. Install dependencies.")
		if self._stream is not None:
			return
		self._buffer = None
		self._stop_event.clear()
		self._stream = sd.InputStream(samplerate=self.sample_rate, channels=self.channels, callback=self._callback)
		self._stream.start()
		self._collect_thread = threading.Thread(target=self._collector_loop, daemon=True)
		self._collect_thread.start()

	def _collector_loop(self) -> None:
		frames: list[np.ndarray] = []
		last_flush = time.time()
		while not self._stop_event.is_set():
			try:
				chunk = self._frames_q.get(timeout=0.1)
				frames.append(chunk)
			except queue.Empty:
				pass
			# Periodically flush to buffer to avoid unbounded memory if long recording
			if (time.time() - last_flush) > 1.0 and frames:
				self._append_to_buffer(frames)
				frames = []
				last_flush = time.time()
		# Final flush
		if frames:
			self._append_to_buffer(frames)

	def _append_to_buffer(self, frames_list: list[np.ndarray]) -> None:
		chunk = np.concatenate(frames_list, axis=0)
		if self._buffer is None:
			self._buffer = chunk
		else:
			self._buffer = np.concatenate([self._buffer, chunk], axis=0)

	def stop(self) -> Tuple[np.ndarray, int]:
		if self._stream is None:
			raise RuntimeError("Recorder is not active")
		self._stop_event.set()
		self._stream.stop()
		self._stream.close()
		self._stream = None
		if self._collect_thread is not None:
			self._collect_thread.join(timeout=2.0)
			self._collect_thread = None
		audio = self._buffer if self._buffer is not None else np.zeros((0, self.channels), dtype=np.float32)
		return audio.astype(np.float32), self.sample_rate

	@staticmethod
	def float_to_int16(audio: np.ndarray) -> np.ndarray:
		audio = np.clip(audio, -1.0, 1.0)
		return (audio * 32767.0).astype(np.int16)

	def save_wav(self, file_path: str, audio: np.ndarray) -> None:
		pcm16 = self.float_to_int16(audio)
		with wave.open(file_path, "wb") as wf:
			wf.setnchannels(self.channels)
			wf.setsampwidth(2)  # 16-bit
			wf.setframerate(self.sample_rate)
			wf.writeframes(pcm16.tobytes())
