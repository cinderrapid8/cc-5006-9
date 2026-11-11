"""Qt application entry point.

Usage: python -m proxyworkstation.app [project_dir]

A project directory holds the SQLite database and generated proxies.
"""

import os
import sys

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    project_dir = argv[1] if len(argv) > 1 else os.path.join(os.getcwd(), "project")
    os.makedirs(project_dir, exist_ok=True)
    db_path = os.path.join(project_dir, "workstation.db")
    proxy_dir = os.path.join(project_dir, "proxies")

    app = QApplication(argv)
    window = MainWindow(db_path, proxy_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
