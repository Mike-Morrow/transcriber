from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

import numpy as np

# PyObjC imports
try:
	from Foundation import NSObject, NSLocale, NSRunLoop, NSDate, NSURL
	from Speech import (
		SFSpeechRecognizer,
		SFSpeechURLRecognitionRequest,
		SFSpeechAudioBufferRecognitionRequest,
		SFSpeechRecognizerAuthorizationStatusAuthorized,
		SFSpeechRecognizerAuthorizationStatusDenied,
		SFSpeechRecognizerAuthorizationStatusRestricted,
		SFSpeechRecognizerAuthorizationStatusNotDetermined,
	)
	from AVFoundation import AVAudioEngine, AVAudioFile
except Exception as exc:  # pragma: no cover
	NSObject = None  # type: ignore


@dataclass
class WordSegment:
	text: str
	start_sec: float
	duration_sec: float
	char_start: int
	char_length: int


@dataclass
class TranscriptionResult:
	text: str
	segments: List[WordSegment]


class AppleSpeechRecognizer:
	def __init__(self, locale: str = "en-US", require_on_device: bool = True) -> None:
		if NSObject is None:
			raise RuntimeError("PyObjC not available. Install 'pyobjc' and run on macOS.")
		self.locale = locale
		self.require_on_device = require_on_device
		self.recognizer = SFSpeechRecognizer.alloc().initWithLocale_(NSLocale.localeWithLocaleIdentifier_(locale))
		if not self.recognizer:
			raise RuntimeError("Failed to initialize SFSpeechRecognizer")
		# Live recognition members
		self._engine: Optional[AVAudioEngine] = None
		self._live_request: Optional[SFSpeechAudioBufferRecognitionRequest] = None
		self._live_task = None
		self._live_callback: Optional[Callable[[str], None]] = None
		self._live_file: Optional[AVAudioFile] = None
		self.live_sample_rate: Optional[int] = None
		self.live_channels: Optional[int] = None

	def _spin_until(self, predicate, timeout: float = 30.0) -> bool:
		deadline = NSDate.dateWithTimeIntervalSinceNow_(timeout)
		while not predicate():
			now = NSDate.date()
			if now.timeIntervalSinceDate_(deadline) > 0:
				return False
			NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
		return True

	def ensure_authorized(self, timeout: float = 30.0) -> None:
		status = type(self.recognizer).authorizationStatus()
		if status == SFSpeechRecognizerAuthorizationStatusAuthorized:
			return
		done = {"complete": False, "status": status}

		def handler(new_status):  # noqa: ANN001
			done["status"] = new_status
			done["complete"] = True

		type(self.recognizer).requestAuthorization_(handler)
		ok = self._spin_until(lambda: done["complete"], timeout=timeout)
		if not ok:
			raise TimeoutError("Timed out waiting for Speech authorization")
		if done["status"] != SFSpeechRecognizerAuthorizationStatusAuthorized:
			raise PermissionError("Speech recognition not authorized")

	def transcribe_file(self, file_path: str) -> TranscriptionResult:
		self.ensure_authorized()
		if not self.recognizer.isAvailable():
			raise RuntimeError("SFSpeechRecognizer is not available")
		url = NSURL.fileURLWithPath_(file_path)
		request = SFSpeechURLRecognitionRequest.alloc().initWithURL_(url)
		# Prefer offline model when available
		try:
			request.setRequiresOnDeviceRecognition_(self.require_on_device)
		except Exception:
			pass
		request.setShouldReportPartialResults_(False)

		result_holder: dict = {"done": False, "result": None, "error": None}

		def completion_handler(result, error):  # noqa: ANN001
			if error is not None:
				result_holder["error"] = error
			else:
				result_holder["result"] = result
			result_holder["done"] = True

		self.recognizer.recognitionTaskWithRequest_resultHandler_(request, completion_handler)
		ok = self._spin_until(lambda: result_holder["done"], timeout=600.0)
		if not ok:
			raise TimeoutError("Transcription timed out")
		if result_holder["error"] is not None:
			raise RuntimeError(f"Transcription error: {result_holder['error']}")

		result = result_holder["result"]
		best = result.bestTranscription()
		full_text = best.formattedString()
		segments_py: List[WordSegment] = []
		for seg in best.segments():
			word = seg.substring()
			start = float(seg.timestamp())
			duration = float(seg.duration())
			rng = seg.substringRange()
			segments_py.append(
				WordSegment(
					text=str(word),
					start_sec=start,
					duration_sec=duration,
					char_start=int(rng.location),
					char_length=int(rng.length),
				)
			)
		return TranscriptionResult(text=str(full_text), segments=segments_py)

	def start_live(self, on_update_text: Callable[[str], None], record_to_path: Optional[str] = None) -> None:
		"""Start live on-device transcription with partial updates.

		If record_to_path is provided, raw mic is written to that WAV via AVAudioFile.
		"""
		self.ensure_authorized()
		if not self.recognizer.isAvailable():
			raise RuntimeError("SFSpeechRecognizer is not available")

		self._engine = AVAudioEngine.alloc().init()
		input_node = self._engine.inputNode()
		if input_node is None:
			raise RuntimeError("No input audio device available")
		format = input_node.outputFormatForBus_(0)
		self.live_sample_rate = int(format.sampleRate())
		self.live_channels = int(format.channelCount())

		self._live_request = SFSpeechAudioBufferRecognitionRequest.alloc().init()
		try:
			self._live_request.setRequiresOnDeviceRecognition_(self.require_on_device)
		except Exception:
			pass
		self._live_request.setShouldReportPartialResults_(True)
		self._live_callback = on_update_text

		# Optional file writer
		self._live_file = None
		if record_to_path is not None:
			try:
				url = NSURL.fileURLWithPath_(record_to_path)
				self._live_file = AVAudioFile.alloc().initForWriting_settings_error_(url, format.settings(), None)
			except Exception:
				self._live_file = None

		def result_handler(result, error):  # noqa: ANN001
			if error is not None:
				return
			if result is None:
				return
			best = result.bestTranscription()
			text = best.formattedString()
			if self._live_callback:
				try:
					self._live_callback(str(text))
				except Exception:
					pass

		self._live_task = self.recognizer.recognitionTaskWithRequest_resultHandler_(self._live_request, result_handler)

		def tap_block(buffer, when):  # noqa: ANN001
			# Feed Speech
			if self._live_request is not None:
				self._live_request.appendAudioPCMBuffer_(buffer)
			# Persist to file
			if self._live_file is not None:
				try:
					self._live_file.writeFromBuffer_error_(buffer, None)
				except Exception:
					pass

		input_node.installTapOnBus_bufferSize_format_block_(0, 1024, format, tap_block)
		self._engine.prepare()
		started = self._engine.startAndReturnError_(None)
		if started is False:
			raise RuntimeError("Failed to start AVAudioEngine")

	def stop_live(self) -> None:
		if self._engine is None:
			return
		try:
			self._engine.inputNode().removeTapOnBus_(0)
		except Exception:
			pass
		try:
			self._engine.stop()
		except Exception:
			pass
		if self._live_request is not None:
			try:
				self._live_request.endAudio()
			except Exception:
				pass
		self._live_request = None
		self._engine = None
		self._live_callback = None
		self._live_file = None
		if self._live_task is not None:
			try:
				self._live_task.cancel()
			except Exception:
				pass
		self._live_task = None
