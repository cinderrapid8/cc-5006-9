"""Qt frame preview widget.

Frames are decoded on demand via ffmpeg (no direct video demuxer dependency):
the current media path plus a frame number always resolves to an exact frame.
A QTimer drives playback; stepping and scrubbing are frame-accurate and show
both the timeline frame and the source frame number through the FrameMap.
"""

import os
import tempfile

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..core.ffutils import extract_frame, probe


class FramePreview(QWidget):
    statusMessage = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._label = QLabel("No media loaded")
        self._label.setMinimumSize(480, 270)
        self._label.setStyleSheet(
            "background:#111; color:#888; font-size:16px;"
            "border:1px solid #333;"
        )
        self._label.setScaledContents(False)
        self._meta_label = QLabel("")
        self._meta_label.setStyleSheet("color:#b0c4de; font-family:monospace;")

        layout = QVBoxLayout(self)
        layout.addWidget(self._label, stretch=1)
        layout.addWidget(self._meta_label)

        self._media_path = None
        self._fps = 24.0
        self._frame_count = 0
        self._position = 0
        self._tmpdir = tempfile.mkdtemp(prefix="proxyws_preview_")
        self._pix_cache = {}

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # -- media loading -----------------------------------------------------

    def load_media(self, path, frame_map=None, resolver=None):
        if not path or not os.path.isfile(path):
            self.show_offline(path)
            return False
        meta = probe(path)
        previous_position = self._position if self._media_path else 0
        self._media_path = path
        self._fps = meta["fps"] or 24.0
        self._frame_count = meta["frame_count"]
        self._frame_map = frame_map
        # Experimental association resolver (content anchor / source-time
        # map). When present it owns proxy->source translation; the V1
        # frame_map dict remains the default and its behavior is unchanged.
        self._resolver = resolver
        self._resolution_cache = {}
        self._pix_cache.clear()
        # Keep the playhead across a proxy/original quality switch.
        self._position = min(previous_position, max(0, self._frame_count - 1))
        self.show_frame(self._position)
        return True

    def show_offline(self, path):
        self._timer.stop()
        self._media_path = None
        self._label.setText(
            f"OFFLINE\n\nOriginal not available:\n{path or '-'}\n\n"
            "Proxy editing remains available; final render is blocked until relink."
        )
        self._meta_label.setText("source offline — hash identity preserved")

    #-- navigation --------------------------------------------------------

    @Slot()
    def show_frame(self, index):
        if not self._media_path:
            return
        index = max(0, min(int(index), self._frame_count - 1))
        self._position = index
        pix = self._decode(index)
        if pix is not None:
            scaled = pix.scaled(
                self._label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._label.setPixmap(scaled)
        source_frame, association = self._source_frame(index)
        source_text = (
            f"{source_frame:04d}" if source_frame is not None else "n/a"
        )
        self._meta_label.setText(
            f"file: {os.path.basename(self._media_path)}   "
            f"proxy/timeline frame: {index:04d}/{self._frame_count - 1:04d}   "
            f"-> source frame: {source_text}   {self._fps:g} fps   {association}"
        )
        self.statusMessage.emit(
            f"frame {index} -> source frame {source_text} [{association}]"
        )

    def _source_frame(self, proxy_index):
        """Return (source_frame_or_None, association_description).

        Three modes, in priority order:

        1. experimental resolver (content anchor / source-time map) - its
           explicit status is surfaced so ambiguous or unmatched regions are
           visible in the UI rather than silently guessed;
        2. V1 FrameMap dict (time-preserving contract) - offset arithmetic;
        3. no mapping (original media) - identity.
        """
        resolver = getattr(self, "_resolver", None)
        if resolver is not None:
            if proxy_index not in self._resolution_cache:
                self._resolution_cache[proxy_index] = (
                    resolver.resolve_proxy_frame(proxy_index)
                )
            resolution = self._resolution_cache[proxy_index]
            label = f"{resolver.scheme}:{resolution.status}"
            if resolution.distance is not None:
                label += f" d={resolution.distance:.2f}"
            return resolution.source_frame, label
        fmap = getattr(self, "_frame_map", None)
        if fmap:
            return proxy_index + int(fmap.get("source_start_offset", 0)), \
                "v1-frame-map:exact"
        return proxy_index, "identity"

    def _decode(self, index):
        if index in self._pix_cache:
            return self._pix_cache[index]
        png = os.path.join(self._tmpdir, f"frame_{index:06d}.png")
        try:
            extract_frame(self._media_path, index, png, self._fps)
            pix = QPixmap(png)
        except Exception as exc:  # corrupt proxy frame must surface, not crash
            self.statusMessage.emit(f"decode error at frame {index}: {exc}")
            return None
        if len(self._pix_cache) > 48:
            self._pix_cache.pop(next(iter(self._pix_cache)))
        self._pix_cache[index] = pix
        return pix

    def step(self, delta):
        self._timer.stop()
        self.show_frame(self._position + delta)

    def play(self):
        if self._media_path:
            self._timer.start(int(1000 / self._fps))

    def pause(self):
        self._timer.stop()

    def _tick(self):
        if self._position >= self._frame_count - 1:
            self._timer.stop()
            return
        self.show_frame(self._position + 1)

    @property
    def position(self):
        return self._position

    def resizeEvent(self, event):  # noqa: N802 (Qt signature)
        super().resizeEvent(event)
        if self._media_path:
            self.show_frame(self._position)
