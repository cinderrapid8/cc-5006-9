"""Main workstation window.

Layout: source library (logical shots + online status) on the left; frame
preview with a proxy/original quality switch and clip editing on the right.
Clip in/out points are entered and stored in *source frame* coordinates, so
flipping the preview quality never alters them.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import MissingSourceError, WorkstationError
from ..core.workstation import Workstation
from .export_dialog import ExportDialog
from .preview import FramePreview
from .relocation import RelocationDialog
from .workers import Worker, run_in_thread


class MainWindow(QMainWindow):
    def __init__(self, db_path, proxy_dir):
        super().__init__()
        self.setWindowTitle("Animation Previz Proxy Editing Workstation")
        self.resize(1280, 820)
        self.ws = Workstation(db_path)
        self.proxy_dir = proxy_dir
        self._threads = []

        self._build_ui()
        self.refresh_sources()
        self.refresh_clips()

    #-- UI construction ---------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        toolbar = QHBoxLayout()
        for text, slot in (
            ("Import original…", self.import_media),
            ("Generate proxies (auto task)", self.generate_proxies),
            ("Relocate paths…", self.relocate),
            ("Export final cut…", self.export_final),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            toolbar.addWidget(btn)
        toolbar.addWidget(QLabel("proxy height:"))
        self.proxy_height_spin = QSpinBox()
        self.proxy_height_spin.setRange(64, 4320)
        self.proxy_height_spin.setValue(270)
        toolbar.addWidget(self.proxy_height_spin)
        toolbar.addStretch(1)
        self.status = QLabel("ready")
        toolbar.addWidget(self.status)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)

        # Left: source library
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Logical shots (identity = SHA-256, not filename)"))
        self.source_table = QTableWidget(0, 6)
        self.source_table.setHorizontalHeaderLabels(
            ["Shot ID", "Original resolution", "FPS", "Frames", "Source status", "Proxy"]
        )
        self.source_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.source_table.itemSelectionChanged.connect(self._load_selected_preview)
        self.source_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        left_layout.addWidget(self.source_table)
        splitter.addWidget(left)

        # Right: preview + clip editing
        right = QWidget()
        right_layout = QVBoxLayout(right)

        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("Preview quality:"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["proxy", "original"])
        self.quality_combo.currentTextChanged.connect(self._load_selected_preview)
        quality_row.addWidget(self.quality_combo)
        quality_row.addWidget(QLabel("Frame association:"))
        self.association_combo = QComboBox()
        # v1-frame-map keeps the original time-preserving contract; the two
        # experimental schemes drive the same FramePreview entry point through
        # the experimental resolver interface.
        self.association_combo.addItems(
            ["v1-frame-map", "source-time-map", "content-anchor"]
        )
        self.association_combo.currentTextChanged.connect(self._load_selected_preview)
        quality_row.addWidget(self.association_combo)
        quality_row.addStretch(1)
        for text, slot in (
            ("⏮", lambda: self.preview.step(-1)),
            ("Play", self._play_pause),
            ("⏭", lambda: self.preview.step(1)),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            quality_row.addWidget(b)
        right_layout.addLayout(quality_row)

        self.preview = FramePreview()
        self.preview.statusMessage.connect(self.status.setText)
        right_layout.addWidget(self.preview, stretch=1)

        clip_row = QHBoxLayout()
        clip_row.addWidget(QLabel("Add clip — shot:"))
        self.clip_shot_combo = QComboBox()
        clip_row.addWidget(self.clip_shot_combo)
        clip_row.addWidget(QLabel("source IN:"))
        self.in_spin = QSpinBox()
        self.in_spin.setRange(0, 1_000_000)
        clip_row.addWidget(self.in_spin)
        clip_row.addWidget(QLabel("source OUT:"))
        self.out_spin = QSpinBox()
        self.out_spin.setRange(1, 1_000_000)
        self.out_spin.setValue(8)
        clip_row.addWidget(self.out_spin)
        add_btn = QPushButton("Add to timeline")
        add_btn.clicked.connect(self.add_clip)
        clip_row.addWidget(add_btn)
        right_layout.addLayout(clip_row)

        right_layout.addWidget(QLabel("Timeline (intervals in source-frame coordinates)"))
        self.clip_table = QTableWidget(0, 5)
        self.clip_table.setHorizontalHeaderLabels(
            ["Clip", "Shot", "Source IN", "Source OUT", "Length (frames)"]
        )
        self.clip_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        right_layout.addWidget(self.clip_table)
        remove_btn = QPushButton("Remove selected clip")
        remove_btn.clicked.connect(self.remove_clip)
        right_layout.addWidget(remove_btn)

        splitter.addWidget(right)
        splitter.setSizes([480, 800])
        root.addWidget(splitter)

    #-- source library ----------------------------------------------------

    def refresh_sources(self):
        sources = self.ws.store.list_sources()
        self.source_table.setRowCount(len(sources))
        current_shots = [self.clip_shot_combo.itemText(i) for i in range(self.clip_shot_combo.count())]
        for row, source in enumerate(sources):
            status = self.ws.store.source_status(source)
            proxy = self.ws.store.get_proxy_for(source["content_hash"])
            proxy_text = (
                f"{proxy['width']}x{proxy['height']} (verified)" if proxy else "—"
            )
            values = [
                source["shot_id"],
                f"{source['width']}x{source['height']}",
                f"{source['fps']:g}",
                str(source["frame_count"]),
                status,
                proxy_text,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, source["content_hash"])
                if col == 4 and status != "online":
                    item.setForeground(Qt.red)
                self.source_table.setItem(row, col, item)
            if source["shot_id"] not in current_shots:
                self.clip_shot_combo.addItem(source["shot_id"])

    def _selected_source(self):
        rows = self.source_table.selectionModel().selectedRows()
        if not rows:
            return None
        content_hash = self.source_table.item(rows[0].row(), 0).data(Qt.UserRole)
        return self.ws.store.get_source_by_hash(content_hash)

    def _load_selected_preview(self):
        source = self._selected_source()
        if not source:
            return
        quality = self.quality_combo.currentText()
        media = self.ws.resolve_media(source["content_hash"], quality)
        if not (media["online"] and os.path.isfile(media["path"])):
            self.preview.show_offline(source["current_path"])
            self.status.setText(
                f"previewing {media['kind']} — in/out points stay in source frames"
            )
            return

        scheme = self.association_combo.currentText()
        resolver = None
        frame_map = media.get("map")
        if scheme in ("source-time-map", "content-anchor") and media["kind"] == "proxy":
            resolver = self._build_experimental_resolver(scheme, source, media["path"])
        self.preview.load_media(
            media["path"], frame_map=frame_map, resolver=resolver
        )
        suffix = f" [{scheme}]" if resolver else ""
        self.status.setText(
            f"previewing {media['kind']}{suffix} — in/out points stay in source frames"
        )

    def _build_experimental_resolver(self, scheme, source, proxy_path):
        """Construct an experimental resolver for the live preview entry point.

        Cache directories live under the project's proxy folder; nothing is
        written to the V1 database. Failures are surfaced, never silently
        downgraded to a guessed mapping.
        """
        from ..experimental import (
            TimeMappingResolver,
            build_content_resolver,
        )

        cache_dir = os.path.join(self.proxy_dir, ".fingerprint_cache")
        if scheme == "source-time-map":
            from ..core.ffutils import probe

            source_meta = {
                "fps": source["fps"],
                "frame_count": source["frame_count"],
            }
            proxy_meta = probe(proxy_path)
            return TimeMappingResolver.from_metas(
                source_meta, proxy_meta, provenance="ui-declared"
            )
        return build_content_resolver(
            source["current_path"], proxy_path, cache_dir=cache_dir
        )

    #-- actions -----------------------------------------------------------

    def import_media(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import original media", "", "Media (*.mp4 *.mov *.mkv)"
        )
        if not paths:
            return
        for path in paths:
            shot_id = os.path.splitext(os.path.basename(path))[0]
            try:
                result = self.ws.import_source(path, shot_id)
            except Exception as exc:
                QMessageBox.critical(self, "Import failed", str(exc))
                continue
            note = " (already known, path updated)" if result["already_known"] else ""
            self.status.setText(f"Imported {shot_id}{note}")
        self.refresh_sources()

    def generate_proxies(self):
        shot_ids = [s["shot_id"] for s in self.ws.store.list_sources()]
        if not shot_ids:
            QMessageBox.information(self, "No media", "Import originals first.")
            return
        self.status.setText("proxy generation running…")
        worker = Worker(
            self.ws.generate_proxies_batch,
            shot_ids,
            self.proxy_dir,
            height=self.proxy_height_spin.value(),
        )
        worker.finished.connect(self._proxies_done)
        worker.failed.connect(lambda msg: QMessageBox.critical(self, "Proxy error", msg))
        thread = run_in_thread(worker)
        self._threads.append(thread)
        thread.start()

    def _proxies_done(self, results):
        self.refresh_sources()
        ok = len(results["succeeded"])
        fail = results["failed"]
        message = f"Generated and verified {ok} proxy/proxies."
        if fail:
            message += "\nRejected/failed:\n" + "\n".join(
                f"• {f['shot_id']}: {f['error']}" for f in fail
            )
        self.status.setText(message)
        QMessageBox.information(self, "Proxy task complete", message)
        self._load_selected_preview()

    def relocate(self):
        dlg = RelocationDialog(self.ws, self)
        dlg.exec()
        self.refresh_sources()
        self._load_selected_preview()

    #-- timeline ----------------------------------------------------------

    def add_clip(self):
        shot_id = self.clip_shot_combo.currentText()
        if not shot_id:
            return
        try:
            self.ws.add_clip(shot_id, self.in_spin.value(), self.out_spin.value())
        except Exception as exc:
            QMessageBox.warning(self, "Invalid clip interval", str(exc))
            return
        self.refresh_clips()
        self.status.setText(
            f"Clip added referencing {shot_id} frames "
            f"[{self.in_spin.value()}, {self.out_spin.value()}) — unchanged by preview quality"
        )

    def remove_clip(self):
        row = self.clip_table.currentRow()
        if row < 0:
            return
        clip_id = int(self.clip_table.item(row, 0).data(Qt.UserRole))
        self.ws.store.remove_clip(clip_id)
        self.refresh_clips()

    def refresh_clips(self):
        clips = self.ws.store.list_clips()
        self.clip_table.setRowCount(len(clips))
        for row, clip in enumerate(clips):
            values = [
                str(clip["clip_id"]),
                clip["shot_id"],
                str(clip["source_in"]),
                str(clip["source_out"]),
                str(clip["source_out"] - clip["source_in"]),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, clip["clip_id"])
                self.clip_table.setItem(row, col, item)

    def _play_pause(self):
        if self.preview._timer.isActive():
            self.preview.pause()
        else:
            self.preview.play()

    #-- export ------------------------------------------------------------

    def export_final(self):
        if not self.ws.store.list_clips():
            QMessageBox.information(self, "Empty timeline", "Add clips before export.")
            return
        dlg = ExportDialog(self.ws, self)
        if not dlg.exec() or not dlg.parameters:
            return
        params = dlg.parameters
        self.status.setText("rendering final cut from originals…")
        worker = Worker(
            self.ws.render_final,
            params["out_path"],
            width=params["width"],
            height=params["height"],
            fps=params["fps"],
            crf=params["crf"],
            preset=params["preset"],
        )
        worker.finished.connect(self._render_done)
        worker.failed.connect(self._render_failed)
        thread = run_in_thread(worker)
        self._threads.append(thread)
        thread.start()

    def _render_done(self, info):
        self.status.setText(
            f"Exported {info['out_path']} ({info['width']}x{info['height']}, "
            f"CRF {info['crf']})"
        )
        accuracy = "FRAME-ACCURATE" if info["frame_accurate"] else "FRAME COUNT MISMATCH"
        QMessageBox.information(
            self,
            "Export complete",
            f"Output: {info['out_path']}\n"
            f"Resolution: {info['width']}x{info['height']} @ {info['fps']:g}fps\n"
            f"CRF: {info['crf']}, preset: {info['preset']}\n"
            f"Segments: {info['segments']}\n"
            f"Frames: {info['actual_frame_count']} actual / "
            f"{info['expected_frames']} expected — {accuracy}",
        )

    def _render_failed(self, message):
        if "offline" in message:
            QMessageBox.critical(
                self,
                "Render blocked — missing originals",
                message + "\n\nUse Relocate paths to re-link. Proxies are never "
                "substituted for original picture.",
            )
        else:
            QMessageBox.critical(self, "Render failed", message)
        self.status.setText("render blocked/failed")

    def closeEvent(self, event):  # noqa: N802
        self.ws.close()
        super().closeEvent(event)
