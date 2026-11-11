"""Relocation dialog: re-link moved media by content hash, never by name."""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class RelocationDialog(QDialog):
    """Lists offline/altered sources and resolves them via hash verification."""

    def __init__(self, workstation, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Path Relocation — re-link by content hash")
        self.resize(760, 420)
        self.ws = workstation
        self._items = []

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "The following originals are offline or altered. Pick the new file "
                "for each; acceptance requires an exact SHA-256 match. A same-named "
                "file with different content is rejected."
            )
        )
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        row = QHBoxLayout()
        self.scan_btn = QPushButton("Auto-scan folder for matching hashes…")
        self.pick_btn = QPushButton("Choose replacement file for selected row…")
        row.addWidget(self.scan_btn)
        row.addWidget(self.pick_btn)
        layout.addLayout(row)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.scan_btn.clicked.connect(self._auto_scan)
        self.pick_btn.clicked.connect(self._pick_file)
        self.refresh()

    def refresh(self):
        self.list_widget.clear()
        self._items = []
        for source in self.ws.store.list_sources():
            status = self.ws.store.source_status(source)
            if status != "online":
                self._items.append(source)
                item = QListWidgetItem(
                    f"{source['shot_id']}  [{status}]  {source['current_path']}\n"
                    f"hash {source['content_hash'][:16]}…"
                )
                item.setData(Qt.UserRole, source["content_hash"])
                self.list_widget.addItem(item)
        if not self._items:
            self.result_label.setText("All originals online — nothing to relocate.")

    def _selected_hash(self):
        item = self.list_widget.currentItem()
        if item is None:
            self.result_label.setText("Select a row first.")
            return None
        return item.data(Qt.UserRole)

    def _try_relocate(self, content_hash, path):
        try:
            self.ws.relocate_source(content_hash, path)
            self.result_label.setText(f"Relinked OK: {path}")
            self.refresh()
            return True
        except Exception as exc:
            self.result_label.setText(f"REJECTED: {exc}")
            return False

    def _pick_file(self):
        content_hash = self._selected_hash()
        if not content_hash:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the moved original", "", "Media (*.mp4 *.mov *.mkv)"
        )
        if path:
            self._try_relocate(content_hash, path)

    def _auto_scan(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose folder to scan")
        if not folder:
            return
        candidates = []
        for dirpath, _, filenames in os.walk(folder):
            for name in filenames:
                candidates.append(os.path.join(dirpath, name))
        resolved = 0
        for source in list(self._items):
            found = self.ws.auto_relocate(source["content_hash"], candidates)
            if found:
                resolved += 1
        self.result_label.setText(
            f"Auto-scan resolved {resolved} of {len(self._items)} offline source(s)."
        )
        self.refresh()
