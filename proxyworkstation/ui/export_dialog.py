"""Export dialog: final-picture parameters and preflight status."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)


class ExportDialog(QDialog):
    def __init__(self, workstation, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Final Export — originals only")
        self.resize(620, 480)
        self.ws = workstation
        self.parameters = None

        layout = QVBoxLayout(self)
        self.preflight_view = QTextEdit()
        self.preflight_view.setReadOnly(True)
        layout.addWidget(QLabel("Preflight (original source availability):"))
        layout.addWidget(self.preflight_view)

        form = QFormLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(64, 7680)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(64, 4320)
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(1.0, 120.0)
        self.fps_spin.setDecimals(3)
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(18)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(
            ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"]
        )
        self.preset_combo.setCurrentText("medium")

        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("Frame rate", self.fps_spin)
        form.addRow("CRF (0=best, 51=smallest)", self.crf_spin)
        form.addRow("Encode preset", self.preset_combo)
        layout.addLayout(form)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit("final_output.mp4")
        browse = QPushButton("Save as…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit)
        path_row.addWidget(browse)
        layout.addLayout(path_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate_defaults()
        self.refresh_preflight()

    def _populate_defaults(self):
        clips = self.ws.store.list_clips()
        if clips:
            first = self.ws.store.get_source_by_hash(clips[0]["content_hash"])
            self.width_spin.setValue(first["width"])
            self.height_spin.setValue(first["height"])
            self.fps_spin.setValue(first["fps"])

    def refresh_preflight(self):
        check = self.ws.preflight()
        if check["ready"]:
            self.preflight_view.setPlainText(
                f"READY: {check['clip_count']} clip(s), every original verified "
                f"by content hash. Proxies will NOT be used as final picture."
            )
        else:
            lines = [
                f"BLOCKED: {len(check['missing'])} source(s) unavailable — "
                "rendering would otherwise silently use low-res proxies:",
                "",
            ]
            for m in check["missing"]:
                lines.append(
                    f"• {m['shot_id']} (clip {m['clip_id']}): {m['reason']}\n  {m['path']}"
                )
            lines += ["", "Open Path Relocation to re-link the originals first."]
            self.preflight_view.setPlainText("\n".join(lines))
        return check["ready"]

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export final cut", "", "MP4 (*.mp4)")
        if path:
            self.path_edit.setText(path)

    def _accept(self):
        if not self.refresh_preflight():
            self.preflight_view.append("\n\nExport refused until all originals are online.")
            return
        self.parameters = {
            "out_path": self.path_edit.text(),
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),
            "fps": self.fps_spin.value(),
            "crf": self.crf_spin.value(),
            "preset": self.preset_combo.currentText(),
        }
        self.accept()
