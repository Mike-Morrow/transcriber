import os
import sys
import tempfile
import wave
import time
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QTextCursor, QIcon, QPixmap, QPainter, QColor, QPen
from PyQt6.QtWidgets import (
	QApplication,
	QFileDialog,
	QHBoxLayout,
	QLabel,
	QListWidget,
	QMainWindow,
	QMessageBox,
	QPushButton,
	QSlider,
	QTextEdit,
	QVBoxLayout,
	QWidget,
	QCheckBox,
	QStyle,
	QToolButton,
	QSplitter,
	QSplitterHandle,
)

# Set up app logger with rotation in user's Library/Logs
LOG_DIR = os.path.expanduser("~/Library/Logs/Transcription Editor")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "session.log")
logger = logging.getLogger("transcription_editor")
if not logger.handlers:
	logger.setLevel(logging.INFO)
	h = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3)
	fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
	h.setFormatter(fmt)
	logger.addHandler(h)
	# Also echo minimal errors to stderr for debugging
	stderr_h = logging.StreamHandler()
	stderr_h.setLevel(logging.ERROR)
	stderr_h.setFormatter(fmt)
	logger.addHandler(stderr_h)

from audio_recorder import AudioRecorder
from apple_speech import AppleSpeechRecognizer, TranscriptionResult
from audio_editor import splice_audio, seconds_to_frames, save_wav, load_wav
from waveform_view import WaveformView

try:
	import sounddevice as sd
except Exception:
	sd = None


@dataclass
class SessionAudio:
	audio: np.ndarray
	sample_rate: int
	channels: int


class HoverHandle(QSplitterHandle):
	def __init__(self, orientation, parent):  # noqa: ANN001
		super().__init__(orientation, parent)
		self._orientation = orientation
		self.base_color = "#DADADA"
		self.hover_color = "#CFCFCF"  # grey hover
		self.press_color = "#9E9E9E"  # darker grey on press
		self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
		self.setMouseTracking(True)
		self._pressed = False
		# Thicker handle for better hit area
		if orientation == Qt.Orientation.Horizontal:
			self.setFixedWidth(8)
		else:
			self.setFixedHeight(8)

	def _current_bg(self) -> str:
		if self._pressed:
			return self.press_color
		# Qt hover state is handled via enter/leave, but repaint always uses this helper
		return self.hover_color if self.underMouse() else self.base_color

	def enterEvent(self, e) -> None:  # noqa: ANN001
		self.update()
		super().enterEvent(e)

	def leaveEvent(self, e) -> None:  # noqa: ANN001
		self.update()
		super().leaveEvent(e)

	def mousePressEvent(self, e) -> None:  # noqa: ANN001
		self._pressed = True
		self.update()
		super().mousePressEvent(e)

	def mouseReleaseEvent(self, e) -> None:  # noqa: ANN001
		self._pressed = False
		self.update()
		super().mouseReleaseEvent(e)

	def paintEvent(self, e) -> None:  # noqa: ANN001
		p = QPainter(self)
		# Background according to state
		p.fillRect(self.rect(), QColor(self._current_bg()))
		# Draw grabber dots
		p.setPen(Qt.PenStyle.NoPen)
		p.setBrush(QColor("#7A7A7A"))
		w = self.width()
		h = self.height()
		if self._orientation == Qt.Orientation.Horizontal:
			cy = h // 2
			for dx in (-6, -2, 2, 6):
				p.drawEllipse((w // 2) + dx - 1, cy - 1, 2, 2)
		else:
			cx = w // 2
			for dy in (-6, -2, 2, 6):
				p.drawEllipse(cx - 1, (h // 2) + dy - 1, 2, 2)
		p.end()


class HoverSplitter(QSplitter):
	def createHandle(self) -> QSplitterHandle:
		return HoverHandle(self.orientation(), self)


class MainWindow(QMainWindow):
	liveText = pyqtSignal(str)

	def __init__(self) -> None:
		super().__init__()
		self.setWindowTitle("Transcription Editor (Local macOS Speech)")

		self.recorder = AudioRecorder(sample_rate=44100, channels=1)
		self.apple = None
		try:
			self.apple = AppleSpeechRecognizer(locale="en-US", require_on_device=True)
		except Exception as e:
			self.apple = None
			QMessageBox.warning(self, "Apple Speech", f"Apple Speech not available: {e}")

		self.session: Optional[SessionAudio] = None
		self.transcription: Optional[TranscriptionResult] = None
		self.selection_time: Optional[Tuple[float, float]] = None

		self._live_wav: Optional[str] = None
		self._using_live: bool = False
		self._play_timer = QTimer(self)
		self._play_timer.setInterval(30)
		self._play_timer.timeout.connect(self._tick_playback)
		self._play_stream = None
		self._play_audio: Optional[np.ndarray] = None  # mono float32
		self._play_sr: int = 0
		self._play_frame: int = 0

		# Re-record state
		self._rerecord_active: bool = False
		self._rerecord_range: Optional[Tuple[float, float]] = None

		# Record flashing indicator
		self._record_flash_timer = QTimer(self)
		self._record_flash_timer.setInterval(1000)
		self._record_flash_timer.timeout.connect(self._tick_record_flash)
		self._record_flash_on = False

		self._build_ui()
		self.liveText.connect(self._on_live_text)

	def _make_record_icon(self, size: int = 18) -> QIcon:
		pm = QPixmap(size, size)
		pm.fill(Qt.GlobalColor.transparent)
		p = QPainter(pm)
		p.setRenderHint(QPainter.RenderHint.Antialiasing)
		p.setPen(QPen(QColor(180, 0, 0), 2))
		p.setBrush(QColor(220, 0, 0))
		r = size // 2 - 2
		p.drawEllipse(pm.rect().center(), r, r)
		p.end()
		return QIcon(pm)

	def _make_pause_icon(self, size: int = 18) -> QIcon:
		pm = QPixmap(size, size)
		pm.fill(Qt.GlobalColor.transparent)
		p = QPainter(pm)
		p.setRenderHint(QPainter.RenderHint.Antialiasing)
		p.setPen(Qt.PenStyle.NoPen)
		p.setBrush(QColor(220, 0, 0))
		bar_w = max(3, size // 5)
		gap = bar_w
		left = (size - (2 * bar_w + gap)) // 2
		p.drawRect(left, 3, bar_w, size - 6)
		p.drawRect(left + bar_w + gap, 3, bar_w, size - 6)
		p.end()
		return QIcon(pm)

	def _tick_record_flash(self) -> None:
		# Toggle border intensity; keep size and center
		self._record_flash_on = not self._record_flash_on
		border = "#FF3B30" if self._record_flash_on else "#FF7A70"
		self.btn_record.setStyleSheet(
			self._record_base_style + f" QToolButton {{ background:#FFE9E8; border:2px solid {border}; }}"
		)

	def _set_record_indicator(self, active: bool) -> None:
		# Flashing red border while recording; show pause icon during active record
		if active:
			self.btn_record.setIcon(self._make_pause_icon(22))
			self._record_flash_on = False
			self._record_flash_timer.start()
			self._tick_record_flash()
		else:
			self._record_flash_timer.stop()
			self.btn_record.setIcon(self._make_record_icon())
			self.btn_record.setStyleSheet(self._record_base_style)

	def _build_ui(self) -> None:
		central = QWidget()
		root = QVBoxLayout(central)

		controls = QHBoxLayout()
		# Icon buttons similar to NLEs
		self.btn_skip_start = QToolButton()
		self.btn_skip_start.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward))
		self.btn_skip_start.setToolTip("Skip to start")

		self.btn_record = QToolButton()
		self.btn_record.setIcon(self._make_record_icon())
		self.btn_record.setIconSize(QSize(20, 20))
		self.btn_record.setToolTip("Record")
		self.btn_record.setFixedSize(36, 36)
		# Base style to keep size/centering consistent in all states
		self._record_base_style = (
			"QToolButton { min-width:36px; min-height:36px; max-width:36px; max-height:36px;"
			" border-radius:12px; padding:0; margin:0; background:transparent; border:1px solid transparent; }"
			" QToolButton:hover { padding:0; margin:0; }"
			" QToolButton:pressed { padding:0; margin:0; border:1px solid transparent; }"
		)
		self.btn_record.setStyleSheet(self._record_base_style)

		self.btn_stop = QToolButton()
		self.btn_stop.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
		self.btn_stop.setToolTip("Stop")

		self.btn_play = QToolButton()
		self.btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
		self.btn_play.setToolTip("Play from cursor")

		self.btn_pause = QToolButton()
		self.btn_pause.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
		self.btn_pause.setToolTip("Pause playback")

		self.btn_skip_end = QToolButton()
		self.btn_skip_end.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward))
		self.btn_skip_end.setToolTip("Skip to end")

		self.btn_export = QPushButton("Export WAV")
		self.btn_import = QPushButton("Import Audio")
		self.btn_clear = QPushButton("Clear")
		self.btn_logs = QPushButton("Logs")
		self.btn_logs.setToolTip("Open logs folder")

		controls.addWidget(self.btn_skip_start)
		controls.addWidget(self.btn_record)
		controls.addWidget(self.btn_stop)
		controls.addWidget(self.btn_play)
		controls.addWidget(self.btn_pause)
		controls.addWidget(self.btn_skip_end)
		controls.addSpacing(12)
		controls.addWidget(self.btn_import)
		controls.addWidget(self.btn_export)
		controls.addWidget(self.btn_clear)
		controls.addStretch(1)
		controls.addWidget(self.btn_logs)
		root.addLayout(controls)

		top = QHBoxLayout()
		left_col = QVBoxLayout()
		right_col = QVBoxLayout()

		self.status_label = QLabel("Idle")
		left_col.addWidget(self.status_label)

		self.text_edit = QTextEdit()
		self.text_edit.setPlaceholderText("Transcript will appear here...")
		self.text_edit.setReadOnly(True)
		left_col.addWidget(self.text_edit)

		self.timestamps_list = QListWidget()
		self.timestamps_list.setMinimumWidth(220)
		right_col.addWidget(QLabel("Timestamps"))
		right_col.addWidget(self.timestamps_list)

		# Wrap columns into widgets for splitter
		left_widget = QWidget()
		left_widget.setLayout(left_col)
		right_widget = QWidget()
		right_widget.setLayout(right_col)

		# Horizontal splitter for transcript and timestamps
		self.hsplit = HoverSplitter(Qt.Orientation.Horizontal)
		self.hsplit.addWidget(left_widget)
		self.hsplit.addWidget(right_widget)
		self.hsplit.setStretchFactor(0, 3)
		self.hsplit.setStretchFactor(1, 1)

		# Waveform
		self.waveform = WaveformView()

		# Vertical splitter combining top (hsplit) and waveform
		self.vsplit = HoverSplitter(Qt.Orientation.Vertical)
		self.vsplit.addWidget(self.hsplit)
		self.vsplit.addWidget(self.waveform)
		self.vsplit.setStretchFactor(0, 3)
		self.vsplit.setStretchFactor(1, 2)

		root.addWidget(self.vsplit)

		self.slider = QSlider(Qt.Orientation.Horizontal)
		self.slider.setRange(0, 1000)
		root.addWidget(self.slider)
		self.slider.hide()

		self.setCentralWidget(central)
		# Enable drag-and-drop import
		self.setAcceptDrops(True)

		# Apply a visible style to splitter handles
		self.setStyleSheet(
			"QSplitter::handle { background:#DADADA; }"
			"QSplitter::handle:horizontal { width:6px; }"
			"QSplitter::handle:vertical { height:6px; }"
			"QSplitter::handle:hover { background:#B9D1FF; }"
			"QSplitter::handle:horizontal:hover { width:8px; }"
			"QSplitter::handle:vertical:hover { height:8px; }"
			"QSplitter::handle:pressed { background:#4A90E2; }"
		)

		# Connections
		self.btn_record.clicked.connect(self.on_record)
		self.btn_stop.clicked.connect(self.on_stop)
		self.btn_play.clicked.connect(self.on_play)
		self.btn_pause.clicked.connect(self.on_pause)
		self.btn_export.clicked.connect(self.on_export)
		self.btn_import.clicked.connect(self.on_import_audio)
		self.btn_clear.clicked.connect(self.on_clear)
		self.btn_skip_start.clicked.connect(self.on_skip_start)
		self.btn_skip_end.clicked.connect(self.on_skip_end)
		self.text_edit.cursorPositionChanged.connect(self.on_selection_changed)
		self.waveform.scrubbed.connect(self.on_scrub)
		self.waveform.selectionChanged.connect(self.on_waveform_selection)
		self.timestamps_list.itemClicked.connect(self.on_timestamp_clicked)
		self.btn_logs.clicked.connect(self.on_open_logs)

	def _stop_playback(self) -> None:
		if sd is None:
			return
		try:
			if self._play_timer.isActive():
				self._play_timer.stop()
			if self._play_stream is not None:
				self._play_stream.stop()
				self._play_stream.close()
				self._play_stream = None
		except Exception:
			self._play_stream = None

	def _start_playback(self, start_sec: float) -> None:
		if sd is None or self.session is None:
			return
		self._stop_playback()
		mono = self.session.audio[:, 0] if self.session.audio.ndim == 2 else self.session.audio
		self._play_audio = mono.astype(np.float32, copy=False)
		self._play_sr = int(self.session.sample_rate)
		self._play_frame = max(0, int(start_sec * self._play_sr))
		self.waveform.set_cursor_time(start_sec)

		def callback(outdata, frames, time_info, status):  # noqa: ANN001
			if self._play_audio is None:
				outdata[:] = 0
				return
			end = min(self._play_frame + frames, len(self._play_audio))
			chunk = self._play_audio[self._play_frame:end]
			out = np.zeros((frames, 1), dtype=np.float32)
			if len(chunk) > 0:
				out[: len(chunk), 0] = chunk
			outdata[:] = out
			self._play_frame = end
			if self._play_frame >= len(self._play_audio):
				raise sd.CallbackStop

		self._play_stream = sd.OutputStream(
			samplerate=self._play_sr,
			channels=1,
			dtype="float32",
			callback=callback,
		)
		self._play_stream.start()
		self._play_timer.start()
		self.status_label.setText("Playing...")

	def on_skip_start(self) -> None:
		if not self.session:
			return
		self.waveform.set_cursor_time(0.0)
		self._start_playback(0.0)

	def on_skip_end(self) -> None:
		if not self.session:
			return
		dur = len(self.session.audio) / self.session.sample_rate
		self._stop_playback()
		self.waveform.set_cursor_time(dur)

	def on_record(self) -> None:
		try:
			# If already recording, treat as pause toggle (invoke Stop behavior)
			if self.recorder.is_recording():
				self.on_stop()
				return
			# If selection exists, arm auto re-record
			auto_range = self._selection_to_time()
			if auto_range is not None:
				self._rerecord_active = True
				self._rerecord_range = auto_range
				logger.info("Auto re-record due to selection [%0.2f,%0.2f]", auto_range[0], auto_range[1])
			self._stop_playback()
			self.recorder.start()
			self._set_record_indicator(True)
			if self._rerecord_active:
				self.status_label.setText("Re-record: recording replacement...")
			else:
				self.status_label.setText("Recording...")
		except Exception as e:
			logger.exception("Record error")
			QMessageBox.critical(self, "Record error", str(e))

	def _on_live_text(self, text: str) -> None:
		# Not used with live disabled; keep to avoid signal issues
		pass

	def on_stop(self) -> None:
		try:
			self._stop_playback()
			if self._rerecord_active:
				if self.recorder.is_recording():
					clip, _sr = self.recorder.stop()
					logger.info("Re-record captured clip frames=%d", len(clip))
					self._set_record_indicator(False)
					clip = clip[:, 0] if clip.ndim == 2 else clip
				else:
					QMessageBox.information(self, "Re-record", "Press Record, speak, then Stop to capture replacement.")
					return
				if not self.session or not self._rerecord_range:
					logger.warning("Re-record with no session/range")
					QMessageBox.warning(self, "Re-record", "No active session or selection to replace.")
					self._rerecord_active = False
					self._rerecord_range = None
					return
				start_sec, end_sec = self._rerecord_range
				new_audio = splice_audio(
					base_audio=self.session.audio,
					sample_rate=self.session.sample_rate,
					start_sec=start_sec,
					end_sec=end_sec,
					insert_audio=clip,
				)
				self.session.audio = new_audio
				self.waveform.set_audio(self.session.audio, self.session.sample_rate)
				self.waveform.set_cursor_time(start_sec)
				self.status_label.setText("Inserted replacement.")
				self._rerecord_active = False
				self._rerecord_range = None
				logger.info("Re-record spliced [%0.2f,%0.2f]", start_sec, end_sec)
				self.on_transcribe()
				return

			if self.recorder.is_recording():
				audio, sr = self.recorder.stop()
				self._set_record_indicator(False)
				logger.info("Primary recording frames=%d sr=%d", len(audio), sr)
				appended = False
				if self.session is None or self.session.audio is None or len(self.session.audio) == 0:
					self.session = SessionAudio(audio=audio, sample_rate=sr, channels=1)
					self.status_label.setText(f"Recorded {len(audio)/sr:.2f}s")
				else:
					dur = len(self.session.audio) / self.session.sample_rate
					cursor = getattr(self.waveform, "_cursor_time", dur)
					if cursor >= (dur - 0.05):
						insert = audio if audio.ndim == 2 else audio[:, None]
						base = self.session.audio if self.session.audio.ndim == 2 else self.session.audio[:, None]
						self.session.audio = np.concatenate([base, insert], axis=0).astype(np.float32)
						self.status_label.setText(f"Appended {len(audio)/sr:.2f}s")
						logger.info("Appended audio seconds=%0.2f", len(audio)/sr)
						appended = True
					else:
						self.session = SessionAudio(audio=audio, sample_rate=sr, channels=1)
						self.status_label.setText(f"Recorded {len(audio)/sr:.2f}s")
			if self.session is not None:
				self.waveform.set_audio(self.session.audio, self.session.sample_rate)
				self.waveform.set_cursor_time(len(self.session.audio) / self.session.sample_rate)
				if 'appended' in locals() and appended:
					# Transcribe immediately after append so new words appear
					self.status_label.setText("Transcribing...")
					self.on_transcribe()
				else:
					QTimer.singleShot(150, self._final_transcribe_if_ready)
		except Exception as e:
			logger.exception("Stop error")
			QMessageBox.critical(self, "Stop error", str(e))

	def _final_transcribe_if_ready(self) -> None:
		if self.apple is not None and self.session is not None and len(self.session.audio) > 0:
			self.on_transcribe()

	def _save_temp_wav(self, audio: np.ndarray, sr: int) -> str:
		fd, tmp_path = tempfile.mkstemp(suffix=".wav")
		os.close(fd)
		with wave.open(tmp_path, "wb") as wf:
			wf.setnchannels(1)
			wf.setsampwidth(2)
			wf.setframerate(sr)
			pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
			wf.writeframes(pcm16.tobytes())
		return tmp_path

	def on_transcribe(self) -> None:
		if self.apple is None:
			QMessageBox.warning(self, "Transcription", "Apple Speech not available.")
			return
		if self.session is None:
			QMessageBox.information(self, "Transcription", "Record audio first.")
			return
		try:
			a = self.session.audio
			mono = a[:, 0] if a.ndim == 2 else a
			wav = self._save_temp_wav(mono, self.session.sample_rate)
			logger.info("Transcribing wav=%s", wav)
			res = self.apple.transcribe_file(wav)
			# Gracefully handle no speech detected
			text_value = (res.text or "").strip()
			if not text_value:
				placeholder = "[no speaking detected]"
				self.transcription = TranscriptionResult(text=placeholder, segments=[])
				self.text_edit.setPlainText(placeholder)
				self._populate_timestamps(self.transcription)
				self.status_label.setText("No speaking detected.")
				return
			self.transcription = res
			self.text_edit.setPlainText(res.text)
			self._populate_timestamps(res)
			# Push words to waveform overlay
			if res.segments:
				words = []
				for seg in res.segments:
					words.append((seg.start_sec, seg.start_sec + seg.duration_sec, seg.text))
				self.waveform.set_transcript(words)
			self.status_label.setText("Transcribed.")
		except Exception as e:
			logger.exception("Transcribe error")
			# If we reach here due to recognizer quirks, still show placeholder rather than an error
			placeholder = "[no speaking detected]"
			self.transcription = TranscriptionResult(text=placeholder, segments=[])
			self.text_edit.setPlainText(placeholder)
			self._populate_timestamps(self.transcription)
			self.status_label.setText("No speaking detected.")

	def _populate_timestamps(self, res: TranscriptionResult) -> None:
		self.timestamps_list.clear()
		for seg in res.segments:
			self.timestamps_list.addItem(f"{seg.start_sec:7.2f}s  {seg.text}")

	def on_timestamp_clicked(self, item) -> None:  # noqa: ANN001
		text = item.text()
		try:
			sec = float(text.split("s")[0])
			self.waveform.set_cursor_time(sec)
			self._start_playback(sec)
		except Exception:
			pass

	def on_scrub(self, time_sec: float) -> None:
		self.waveform.set_cursor_time(time_sec)
		# Do not auto-play on waveform click; wait for explicit Play

	def _tick_playback(self) -> None:
		if self._play_audio is None or self._play_stream is None:
			self._play_timer.stop()
			return
		try:
			pos_sec = self._play_frame / max(1, self._play_sr)
			self.waveform.set_cursor_time(pos_sec)
			if self._play_frame >= len(self._play_audio):
				self._stop_playback()
				self.waveform.set_cursor_time(pos_sec)
		except Exception as _e:
			self._play_timer.stop()

	def _selection_to_time(self) -> Optional[Tuple[float, float]]:
		if self.transcription is None:
			return None
		cursor = self.text_edit.textCursor()
		start = min(cursor.selectionStart(), cursor.selectionEnd())
		end = max(cursor.selectionStart(), cursor.selectionEnd())
		if start == end:
			return None
		start_t = None
		end_t = None
		for seg in self.transcription.segments:
			seg_start_char = seg.char_start
			seg_end_char = seg.char_start + seg.char_length
			if start_t is None and start < seg_end_char:
				start_t = seg.start_sec
			if end_t is None and end <= seg_end_char:
				end_t = seg.start_sec + seg.duration_sec
				break
		if start_t is None:
			start_t = 0.0
		if end_t is None and self.session:
			end_t = len(self.session.audio) / self.session.sample_rate
		return (start_t, end_t)

	def on_selection_changed(self) -> None:
		self.selection_time = self._selection_to_time()
		# Reflect selection on waveform
		if self.selection_time is not None and self.session is not None:
			self.waveform.set_selection(self.selection_time[0], self.selection_time[1])
		else:
			self.waveform.set_selection(None, None)

	def on_rerecord(self) -> None:
		if self.session is None or self.selection_time is None:
			QMessageBox.information(self, "Re-record", "Select transcript text, then click Re-record.")
			return
		start_sec, end_sec = self.selection_time
		self._rerecord_active = True
		self._rerecord_range = (start_sec, end_sec)
		QMessageBox.information(self, "Re-record", "Press Record, speak the replacement, then press Stop.")

	def on_play(self) -> None:
		if self.session is None or sd is None:
			return
		# If cursor is at/near end, start from beginning
		dur = len(self.session.audio) / self.session.sample_rate if self.session and self.session.sample_rate > 0 else 0.0
		start_sec = getattr(self.waveform, "_cursor_time", 0.0)
		if dur > 0.0 and start_sec >= (dur - 0.01):
			start_sec = 0.0
		self._start_playback(start_sec)

	def on_pause(self) -> None:
		# Stop audio stream but keep current frame/cursor for resume
		if self._play_stream is not None:
			try:
				self._play_stream.stop()
			except Exception:
				pass
		self._play_stream = None
		if self._play_timer.isActive():
			self._play_timer.stop()
		self.status_label.setText("Paused")

	def on_export(self) -> None:
		if self.session is None:
			return
		file_path, _ = QFileDialog.getSaveFileName(self, "Export WAV", "", "WAV Files (*.wav)")
		if not file_path:
			return
		try:
			save_wav(file_path, self.session.audio, self.session.sample_rate, self.session.channels)
			self.status_label.setText(f"Exported: {file_path}")
		except Exception as e:
			QMessageBox.critical(self, "Export error", str(e))

	def on_open_logs(self) -> None:
		try:
			logger.info("Open logs requested")
			# Open the logs directory in Finder
			from subprocess import run
			run(["open", LOG_DIR])
		except Exception as e:
			QMessageBox.critical(self, "Logs", str(e))

	def on_waveform_selection(self, start: float, end: float) -> None:
		# Update selection state from waveform drag
		if start == end:
			self.selection_time = None
			return
		self.selection_time = (min(start, end), max(start, end))

	def keyPressEvent(self, event) -> None:  # noqa: ANN001
		key = event.key()
		if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
			self._delete_selection()
			return
		super().keyPressEvent(event)

	def _delete_selection(self) -> None:
		if self.session is None:
			return
		# Prefer waveform selection or transcript selection mapping
		sel = self.selection_time or self._selection_to_time()
		if sel is None:
			return
		start_sec, end_sec = sel
		if end_sec <= start_sec:
			return
		try:
			# Splice out the selected region (replace with 0-duration)
			blank = np.zeros((0,), dtype=np.float32)
			new_audio = splice_audio(
				base_audio=self.session.audio,
				sample_rate=self.session.sample_rate,
				start_sec=start_sec,
				end_sec=end_sec,
				insert_audio=blank,
			)
			self.session.audio = new_audio
			self.waveform.set_audio(self.session.audio, self.session.sample_rate)
			self.waveform.set_selection(None, None)
			self.waveform.set_cursor_time(start_sec)
			self.status_label.setText("Deleted selection.")
			self.selection_time = None
			self.on_transcribe()
		except Exception as e:
			logger.exception("Delete selection error")
			QMessageBox.critical(self, "Delete error", str(e))

	def on_import_audio(self) -> None:
		file_path, _ = QFileDialog.getOpenFileName(self, "Import Audio", "", "WAV Files (*.wav)")
		if not file_path:
			return
		self._load_audio_file(file_path)

	def _load_audio_file(self, file_path: str) -> None:
		try:
			logger.info("Importing audio file: %s", file_path)
			audio, sr, ch = load_wav(file_path)
			self.session = SessionAudio(audio=audio, sample_rate=sr, channels=ch)
			self.waveform.set_audio(self.session.audio, self.session.sample_rate)
			self.waveform.set_cursor_time(0.0)
			self.status_label.setText(f"Loaded {os.path.basename(file_path)}")
			self.on_transcribe()
		except Exception as e:
			logger.exception("Import failed")
			QMessageBox.critical(self, "Import error", str(e))

	def dragEnterEvent(self, event) -> None:  # noqa: ANN001
		md = event.mimeData()
		if md.hasUrls():
			for url in md.urls():
				if url.isLocalFile() and url.toLocalFile().lower().endswith(".wav"):
					event.acceptProposedAction()
					return
		event.ignore()

	def dropEvent(self, event) -> None:  # noqa: ANN001
		md = event.mimeData()
		if md.hasUrls():
			for url in md.urls():
				if url.isLocalFile() and url.toLocalFile().lower().endswith(".wav"):
					self._load_audio_file(url.toLocalFile())
					event.acceptProposedAction()
					return
		event.ignore()

	def on_clear(self) -> None:
		# Ask for confirmation if there is any content
		has_audio = self.session is not None and self.session.audio is not None and len(self.session.audio) > 0
		has_text = (self.text_edit.toPlainText() or '').strip() != ''
		if not (has_audio or has_text):
			return
		resp = QMessageBox.question(self, "Clear project", "Delete all audio and transcript?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
		if resp != QMessageBox.StandardButton.Yes:
			return
		# Stop playback/recording
		self._stop_playback()
		try:
			if self.recorder.is_recording():
				self.recorder.stop()
				self._set_record_indicator(False)
		except Exception:
			pass
		# Clear state
		self.session = None
		self.transcription = None
		self.selection_time = None
		self.text_edit.clear()
		self.timestamps_list.clear()
		self.waveform.set_selection(None, None)
		self.waveform.set_transcript([])
		# Set an empty waveform
		self.waveform.set_audio(np.zeros((0,), dtype=np.float32), 44100)
		self.waveform.set_cursor_time(0.0)
		self.status_label.setText("Cleared.")


def main() -> None:
	app = QApplication(sys.argv)
	win = MainWindow()
	win.resize(1100, 720)
	win.show()
	sys.exit(app.exec())


if __name__ == "__main__":
	main()
