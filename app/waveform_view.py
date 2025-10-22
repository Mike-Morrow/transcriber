from __future__ import annotations

from typing import Optional, List, Tuple

import numpy as np

from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QEvent
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import QWidget, QGestureEvent


class WaveformView(QWidget):
	"""Simple waveform viewer with mouse scrubbing, selection, zoom, and scroll.

	- Click: move playhead and emit scrubbed(time)
	- Drag: select a range and emit selectionChanged(start, end)
	- Wheel: horizontal scroll; Ctrl/Cmd + wheel: zoom around cursor
	- Pinch: trackpad pinch to zoom around gesture center
	- Double-click: reset zoom and scroll
	- set_transcript: provide word timings for overlay labels
	"""

	scrubbed = pyqtSignal(float)
	selectionChanged = pyqtSignal(float, float)

	def __init__(self, parent=None) -> None:  # noqa: ANN001
		super().__init__(parent)
		self.setMinimumHeight(120)
		self._audio: Optional[np.ndarray] = None
		self._sr: int = 44100
		self._cursor_time: float = 0.0
		self._sel_start: Optional[float] = None
		self._sel_end: Optional[float] = None
		self._dragging: bool = False
		self._word_items: Optional[List[Tuple[float, float, str]]] = None  # (start, end, text)
		# Viewport state
		self._view_start: float = 0.0
		self._view_dur: Optional[float] = None
		# Enable pinch gesture
		self.grabGesture(Qt.GestureType.PinchGesture)

	def event(self, e) -> bool:  # noqa: ANN001
		if e.type() == QEvent.Type.Gesture:
			return self._handle_gesture(e) or True
		return super().event(e)

	def _handle_gesture(self, e: QGestureEvent) -> bool:
		pinch = e.gesture(Qt.GestureType.PinchGesture)
		if pinch is None:
			return False
		dur = self._duration()
		if dur <= 0:
			return False
		# Initialize view if showing full
		vs, ve = self._visible_range()
		vr = ve - vs if self._view_dur is not None else dur
		if self._view_dur is None:
			self._view_dur = vr
			self._view_start = vs
		scale = float(getattr(pinch, 'scaleFactor')()) if hasattr(pinch, 'scaleFactor') else 1.0
		if scale <= 0:
			scale = 1.0
		factor = 1.0 / scale  # pinch out (>1) zooms in
		new_dur = np.clip(self._view_dur * factor, min(0.1, dur), dur)
		center = pinch.centerPoint()
		cx = center.x() if center is not None else self.width() / 2
		cursor_t = self._time_at_x(cx)
		rel = (cursor_t - self._view_start) / max(1e-9, self._view_dur)
		self._view_start = cursor_t - rel * new_dur
		self._view_dur = new_dur
		self._clamp_view()
		self.update()
		return True

	def _duration(self) -> float:
		if self._audio is None or self._sr <= 0:
			return 0.0
		return len(self._audio) / self._sr

	def _visible_range(self) -> tuple[float, float]:
		dur = self._duration()
		if dur <= 0:
			return (0.0, 0.0)
		if self._view_dur is None or self._view_dur <= 0 or self._view_dur >= dur:
			return (0.0, dur)
		start = max(0.0, min(self._view_start, max(0.0, dur - self._view_dur)))
		return (start, min(dur, start + self._view_dur))

	def set_audio(self, audio: np.ndarray, sample_rate: int) -> None:
		if audio.ndim == 2:
			audio = audio[:, 0]
		self._audio = audio.astype(np.float32)
		self._sr = int(sample_rate)
		self._cursor_time = 0.0
		self._sel_start = None
		self._sel_end = None
		# Reset view to full duration
		self._view_start = 0.0
		self._view_dur = None
		self.update()

	def set_transcript(self, words: List[Tuple[float, float, str]]) -> None:
		"""Provide word timings as (start_sec, end_sec, text)."""
		self._word_items = words
		self.update()

	def set_cursor_time(self, time_sec: float) -> None:
		self._cursor_time = max(0.0, float(time_sec))
		self.update()

	def set_selection(self, start_sec: Optional[float], end_sec: Optional[float]) -> None:
		self._sel_start = start_sec
		self._sel_end = end_sec
		self.update()

	def _clamp_view(self) -> None:
		dur = self._duration()
		if dur <= 0:
			self._view_start = 0.0
			self._view_dur = None
			return
		if self._view_dur is not None:
			self._view_dur = max(min(self._view_dur, dur), min(0.1, dur))
			self._view_start = max(0.0, min(self._view_start, dur - self._view_dur))
		else:
			self._view_start = 0.0

	def _time_at_x(self, x: float) -> float:
		dur = self._duration()
		if dur <= 0 or self._sr <= 0:
			return 0.0
		vs, ve = self._visible_range()
		vr = max(1e-9, ve - vs)
		pos = np.clip(x / max(1, self.width()), 0.0, 1.0)
		return float(vs + pos * vr)

	def _x_at_time(self, t: float, w: int) -> int:
		vs, ve = self._visible_range()
		vr = max(1e-9, ve - vs)
		pos = np.clip((t - vs) / vr, 0.0, 1.0)
		return int(pos * w)

	def mousePressEvent(self, event) -> None:  # noqa: ANN001
		if self._duration() <= 0:
			return
		if event.button() == Qt.MouseButton.LeftButton:
			t = self._time_at_x(event.position().x())
			self._cursor_time = float(t)
			# Start drag selection
			self._dragging = True
			self._sel_start = t
			self._sel_end = t
			self.selectionChanged.emit(self._sel_start, self._sel_end)
			self.scrubbed.emit(self._cursor_time)
			self.update()

	def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
		if not self._dragging or self._duration() <= 0:
			return
		t = self._time_at_x(event.position().x())
		self._sel_end = float(t)
		self.selectionChanged.emit(min(self._sel_start, self._sel_end), max(self._sel_start, self._sel_end))
		self.update()

	def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
		if self._dragging:
			self._dragging = False
			self.update()

	def wheelEvent(self, event) -> None:  # noqa: ANN001
		# Prefer horizontal trackpad scroll; fall back to vertical
		px = event.pixelDelta().x()
		py = event.pixelDelta().y()
		ax = event.angleDelta().x()
		ay = event.angleDelta().y()
		delta_x = px if px else ax
		delta_y = py if py else ay
		mods = event.modifiers()
		dur = self._duration()
		if dur <= 0:
			return
		# Zoom with Ctrl/Cmd (use sign from vertical if present, else horizontal)
		if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
			sign = 1 if (delta_y if delta_y else delta_x) > 0 else -1
			factor = 0.95 if sign > 0 else 1.05
			vs, ve = self._visible_range()
			vr = ve - vs if self._view_dur is not None else dur
			if self._view_dur is None:
				self._view_dur = vr
				self._view_start = vs
			cursor_t = self._time_at_x(event.position().x())
			new_dur = np.clip(self._view_dur * factor, min(0.05, dur), dur)
			rel = (cursor_t - self._view_start) / max(1e-9, self._view_dur)
			self._view_start = cursor_t - rel * new_dur
			self._view_dur = new_dur
			self._clamp_view()
			self.update()
			return
		# Horizontal pan: small, smooth step scaled by delta_x
		vs, ve = self._visible_range()
		vr = ve - vs if self._view_dur is not None else dur
		if self._view_dur is None:
			self._view_dur = vr
			self._view_start = vs
		# angleDelta is in 1/8th degrees; 120 per notch. pixelDelta is pixels
		if px:
			step = (px / 300.0) * self._view_dur  # gentle scaling
		else:
			step = (ax / 120.0) * 0.03 * self._view_dur
		self._view_start -= step
		self._clamp_view()
		self.update()

	def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
		# Reset zoom and scroll
		self._view_start = 0.0
		self._view_dur = None
		self.update()

	def resizeEvent(self, event) -> None:  # noqa: ANN001
		super().resizeEvent(event)
		self.update()

	def _draw_time_axis(self, p: QPainter, w: int, h: int) -> None:
		dur = self._duration()
		if dur <= 0:
			return
		vs, ve = self._visible_range()
		vr = max(1e-9, ve - vs)
		# Choose tick spacing to target ~100px between ticks
		px_per_sec = w / vr
		target_px = 100.0
		candidates = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]
		step = candidates[-1]
		for c in candidates:
			if c * px_per_sec >= target_px:
				step = c
				break
		axis_y = h - 14
		pen = QPen(QColor(210, 210, 210))
		pen.setWidth(1)
		p.setPen(pen)
		p.drawLine(0, axis_y, w, axis_y)
		label_pen = QPen(QColor(130, 130, 130))
		p.setPen(label_pen)
		def fmt(t: float) -> str:
			m = int(t) // 60
			s = int(t) % 60
			return f"{m}:{s:02d}"
		# First tick at ceil(vs/step)*step
		first = np.ceil(vs / step) * step
		t = max(0.0, first)
		while t <= ve + 1e-6:
			x = self._x_at_time(t, w)
			p.drawLine(x, axis_y, x, axis_y + 6)
			p.drawText(x + 2, axis_y + 12, fmt(t))
			t += step

	def _draw_envelope(self, p: QPainter, w: int, h: int) -> None:
		dur = self._duration()
		if dur <= 0:
			return
		vs, ve = self._visible_range()
		start_idx = int(vs * self._sr)
		end_idx = int(ve * self._sr)
		chunk = self._audio[start_idx:end_idx] if (self._audio is not None) else None
		if chunk is None or len(chunk) == 0:
			return
		bucket = max(1, len(chunk) // max(1, w))
		trim = (len(chunk) // bucket) * bucket
		seg = chunk[:trim].reshape(-1, bucket)
		env = np.max(np.abs(seg), axis=1)
		pen = QPen(QColor(60, 120, 200))
		pen.setWidth(1)
		p.setPen(pen)
		mid = (h - 16) / 2.0
		for i, amp in enumerate(env):
			x = int(i * (w / len(env)))
			bar_h = float(amp) * ((h - 20) * 0.9) / 2.0
			p.drawLine(x, int(mid - bar_h), x, int(mid + bar_h))

	def _draw_words(self, p: QPainter, w: int, h: int) -> None:
		if not self._word_items:
			return
		vs, ve = self._visible_range()
		baseline_y = h - 28
		min_gap_px = 40
		last_x = -1e9
		for start, end, text in self._word_items:
			if end < vs or start > ve:
				continue
			xs = self._x_at_time(start, w)
			xe = self._x_at_time(end, w)
			if xs - last_x < min_gap_px:
				continue
			# Highlight if within selection
			if self._sel_start is not None and self._sel_end is not None:
				if not (end <= min(self._sel_start, self._sel_end) or start >= max(self._sel_start, self._sel_end)):
					p.fillRect(QRectF(xs, baseline_y - 14, max(20, xe - xs), 16), QColor(255, 0, 0, 30))
			p.drawText(xs, baseline_y, text)
			last_x = xs

	def paintEvent(self, event) -> None:  # noqa: ANN001
		p = QPainter(self)
		p.fillRect(self.rect(), QColor(245, 245, 245))
		w = self.width()
		h = self.height()
		# Envelope bars
		self._draw_envelope(p, w, h)
		# Selection overlay
		if self._sel_start is not None and self._sel_end is not None:
			left_x = self._x_at_time(min(self._sel_start, self._sel_end), w)
			right_x = self._x_at_time(max(self._sel_start, self._sel_end), w)
			p.fillRect(QRectF(left_x, 0, max(1, right_x - left_x), h - 16), QColor(255, 0, 0, 40))
			pen = QPen(QColor(255, 0, 0, 140))
			pen.setWidth(2)
			pen.setStyle(Qt.PenStyle.DashLine)
			p.setPen(pen)
			p.drawLine(left_x, 0, left_x, h - 16)
			p.drawLine(right_x, 0, right_x, h - 16)
		# Cursor line
		x = self._x_at_time(self._cursor_time, w)
		pen = QPen(QColor(220, 80, 80))
		pen.setWidth(2)
		p.setPen(pen)
		p.drawLine(x, 0, x, h - 16)
		# Words and time axis
		self._draw_words(p, w, h)
		self._draw_time_axis(p, w, h)
		p.end()
