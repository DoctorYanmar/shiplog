#!/usr/bin/env python3
"""ShipLog — Marine Project & Daily Task Manager.

Entry point: initializes logging, database, settings, theme, and launches the GUI.
"""

import sys
import os
import logging
from pathlib import Path

# Ensure the parent directory is in sys.path so 'shiplog' is importable as a package
# (needed when running as `python main.py` from inside the shiplog/ folder)
_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from PyQt6.QtWidgets import QApplication

from shiplog.core.database import Database
from shiplog.ui.settings_dialog import load_settings
from shiplog.ui.main_window import MainWindow


def setup_logging():
    log_dir = Path.home() / "ShipLog" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "shiplog.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(log_file)),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_theme(app: QApplication, theme_name: str, font_size: int = 14):
    theme_dir = Path(__file__).parent / "assets" / "themes"
    theme_file = theme_dir / f"{theme_name}.qss"
    if theme_file.exists():
        with open(theme_file, "r") as f:
            qss = f.read()
        qss = qss.replace("font-size: 14px;", f"font-size: {font_size}px;")
        app.setStyleSheet(qss)


def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("ShipLog starting...")

    # Ensure base directory exists
    base = Path.home() / "ShipLog"
    base.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setApplicationName("ShipLog")
    app.setOrganizationName("ShipLog")

    # Load settings
    settings = load_settings()

    # Apply theme
    load_theme(app, settings.get("theme", "dark"), settings.get("font_size", 14))

    # Initialize database
    db = Database()

    # Create and show main window
    window = MainWindow(db, settings)
    window.show()

    exit_code = app.exec()

    # Cleanup
    db.close()
    logger.info("ShipLog exiting with code %d", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
