"""Settings dialog with tabbed configuration panels."""

import json
import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox,
    QSpinBox, QSlider, QFormLayout, QFileDialog, QGroupBox,
    QDialogButtonBox, QFontComboBox, QTextEdit, QMessageBox,
)

from shiplog.core.ai_service import (
    DEFAULT_SYSTEM_PROMPT, AITestWorker,
    load_token_usage, reset_token_usage,
)

DEFAULT_SETTINGS = {
    "theme": "dark",
    "font_family": "Segoe UI",
    "font_size": 14,
    "accent_color": "#89b4fa",
    "dashboard_sort": "priority_deadline",
    "card_size": "normal",
    "ai_enabled": False,
    "ai_api_key": "",
    "ai_model": "google/gemma-3-27b-it",
    "ai_frequency": "on_open",
    "ai_system_prompt": DEFAULT_SYSTEM_PROMPT,
    "notifications_enabled": True,
    "reminder_lead_hours": 24,
    "base_folder": str(Path.home() / "ShipLog" / "projects"),
    "ml_search_enabled": False,
}


def get_settings_path() -> Path:
    return Path.home() / "ShipLog" / "settings.json"


def load_settings() -> dict:
    path = get_settings_path()
    settings = dict(DEFAULT_SETTINGS)
    if path.exists():
        try:
            with open(path, "r") as f:
                saved = json.load(f)
            settings.update(saved)
        except Exception:
            pass
    return settings


def save_settings(settings: dict) -> None:
    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)


class SettingsDialog(QDialog):
    """Settings dialog organized in tabs."""

    settings_changed = pyqtSignal(dict)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(600, 520)
        self.settings = dict(settings)
        self._test_worker = None

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_appearance_tab(), "Appearance")
        self.tabs.addTab(self._build_dashboard_tab(), "Dashboard")
        self.tabs.addTab(self._build_ai_tab(), "AI Module")
        self.tabs.addTab(self._build_search_tab(), "Search")
        self.tabs.addTab(self._build_notifications_tab(), "Notifications")
        self.tabs.addTab(self._build_paths_tab(), "Paths")
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_appearance_tab(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "light"])
        idx = self.theme_combo.findText(self.settings.get("theme", "dark"))
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        layout.addRow("Theme:", self.theme_combo)

        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(
            self.font_combo.currentFont()  # default
        )
        layout.addRow("Font Family:", self.font_combo)

        self.font_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_size_slider.setRange(10, 24)
        self.font_size_slider.setValue(self.settings.get("font_size", 14))
        self.font_size_label = QLabel(str(self.font_size_slider.value()))
        self.font_size_slider.valueChanged.connect(
            lambda v: self.font_size_label.setText(str(v))
        )
        size_row = QHBoxLayout()
        size_row.addWidget(self.font_size_slider)
        size_row.addWidget(self.font_size_label)
        layout.addRow("Font Size:", size_row)

        return w

    def _build_dashboard_tab(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["priority_deadline", "last_modified", "title_az"])
        idx = self.sort_combo.findText(self.settings.get("dashboard_sort", "priority_deadline"))
        if idx >= 0:
            self.sort_combo.setCurrentIndex(idx)
        layout.addRow("Default Sort:", self.sort_combo)

        self.card_size_combo = QComboBox()
        self.card_size_combo.addItems(["compact", "normal", "expanded"])
        idx = self.card_size_combo.findText(self.settings.get("card_size", "normal"))
        if idx >= 0:
            self.card_size_combo.setCurrentIndex(idx)
        layout.addRow("Card Size:", self.card_size_combo)

        return w

    def _build_ai_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # ── Connection settings ──
        conn_group = QGroupBox("Connection")
        conn_layout = QFormLayout(conn_group)

        self.ai_enabled_cb = QCheckBox("Enable AI Summaries")
        self.ai_enabled_cb.setChecked(self.settings.get("ai_enabled", False))
        conn_layout.addRow(self.ai_enabled_cb)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setText(self.settings.get("ai_api_key", ""))
        self.api_key_edit.setPlaceholderText("sk-or-...")
        conn_layout.addRow("API Key:", self.api_key_edit)

        self.model_edit = QLineEdit()
        self.model_edit.setText(self.settings.get("ai_model", "google/gemma-3-27b-it"))
        self.model_edit.setPlaceholderText("e.g. google/gemma-3-27b-it")
        conn_layout.addRow("Model:", self.model_edit)

        self.freq_combo = QComboBox()
        self.freq_combo.addItems(["on_request", "on_open", "hourly", "daily"])
        idx = self.freq_combo.findText(self.settings.get("ai_frequency", "on_open"))
        if idx >= 0:
            self.freq_combo.setCurrentIndex(idx)
        conn_layout.addRow("Frequency:", self.freq_combo)

        # Test button
        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._test_ai)
        test_row.addWidget(self.test_btn)
        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        test_row.addWidget(self.test_status, 1)
        conn_layout.addRow(test_row)

        layout.addWidget(conn_group)

        # ── System Prompt ──
        prompt_group = QGroupBox("System Prompt")
        prompt_layout = QVBoxLayout(prompt_group)
        self.system_prompt_edit = QTextEdit()
        self.system_prompt_edit.setPlainText(
            self.settings.get("ai_system_prompt", DEFAULT_SYSTEM_PROMPT)
        )
        self.system_prompt_edit.setMinimumHeight(80)
        prompt_layout.addWidget(self.system_prompt_edit)

        reset_prompt_btn = QPushButton("Reset to Default")
        reset_prompt_btn.clicked.connect(
            lambda: self.system_prompt_edit.setPlainText(DEFAULT_SYSTEM_PROMPT)
        )
        prompt_layout.addWidget(reset_prompt_btn)
        layout.addWidget(prompt_group)

        # ── Token Usage ──
        usage_group = QGroupBox("Token Usage")
        usage_layout = QFormLayout(usage_group)

        usage = load_token_usage()
        self.total_in_label = QLabel(f"{usage.get('total_in', 0):,}")
        self.total_out_label = QLabel(f"{usage.get('total_out', 0):,}")
        self.daily_in_label = QLabel(f"{usage.get('daily_in', 0):,}")
        self.daily_out_label = QLabel(f"{usage.get('daily_out', 0):,}")

        usage_layout.addRow("Total input tokens:", self.total_in_label)
        usage_layout.addRow("Total output tokens:", self.total_out_label)
        usage_layout.addRow("Today input tokens:", self.daily_in_label)
        usage_layout.addRow("Today output tokens:", self.daily_out_label)

        reset_usage_btn = QPushButton("Reset Counters")
        reset_usage_btn.clicked.connect(self._reset_usage)
        usage_layout.addRow(reset_usage_btn)

        layout.addWidget(usage_group)
        layout.addStretch()

        return w

    def _test_ai(self):
        api_key = self.api_key_edit.text().strip()
        model = self.model_edit.text().strip()
        if not api_key or not model:
            self.test_status.setText("Enter API key and model first.")
            self.test_status.setStyleSheet("color: #f38ba8;")
            return

        self.test_btn.setEnabled(False)
        self.test_status.setText("Testing...")
        self.test_status.setStyleSheet("")

        self._test_worker = AITestWorker(api_key, model, parent=self)
        self._test_worker.test_result.connect(self._on_test_result)
        self._test_worker.start()

    def _on_test_result(self, success: bool, message: str):
        self.test_btn.setEnabled(True)
        if success:
            self.test_status.setText(message)
            self.test_status.setStyleSheet("color: #a6e3a1;")
        else:
            self.test_status.setText(f"Failed: {message}")
            self.test_status.setStyleSheet("color: #f38ba8;")
        self._refresh_usage()

    def _reset_usage(self):
        reset_token_usage()
        self._refresh_usage()

    def _refresh_usage(self):
        usage = load_token_usage()
        self.total_in_label.setText(f"{usage.get('total_in', 0):,}")
        self.total_out_label.setText(f"{usage.get('total_out', 0):,}")
        self.daily_in_label.setText(f"{usage.get('daily_in', 0):,}")
        self.daily_out_label.setText(f"{usage.get('daily_out', 0):,}")

    def _build_search_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # ── ML Semantic Search ──
        ml_group = QGroupBox("ML Semantic Search")
        ml_layout = QVBoxLayout(ml_group)

        self.ml_search_cb = QCheckBox("Enable ML Semantic Search")
        self.ml_search_cb.setChecked(self.settings.get("ml_search_enabled", False))
        self.ml_search_cb.setToolTip(
            "Uses TF-IDF (scikit-learn) for context-aware search.\n"
            "Ranks results by term importance and relevance.\n"
            "Lightweight — no model download needed.\n"
            "Index is built from your content on demand."
        )
        ml_layout.addWidget(self.ml_search_cb)

        # Check if scikit-learn is available (used for TF-IDF ML search)
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            ml_status = QLabel("scikit-learn: installed")
            ml_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")
        except Exception:
            ml_status = QLabel(
                "scikit-learn: not available\n"
                "Install with: pip install scikit-learn"
            )
            ml_status.setStyleSheet("color: #f38ba8; font-size: 11px;")
        ml_status.setWordWrap(True)
        ml_layout.addWidget(ml_status)

        ml_info = QLabel(
            "When enabled, a 'Semantic (ML)' search mode appears in the Search tab.\n"
            "Uses TF-IDF with cosine similarity for context-aware ranking.\n"
            "No model download needed — built from your indexed content."
        )
        ml_info.setStyleSheet("color: #6c7086; font-size: 11px;")
        ml_info.setWordWrap(True)
        ml_layout.addWidget(ml_info)

        layout.addWidget(ml_group)

        # ── Search info ──
        info_group = QGroupBox("Search Information")
        info_layout = QVBoxLayout(info_group)
        info_text = QLabel(
            "ShipLog uses SQLite FTS5 for fast full-text search with stemming.\n"
            "The search index covers: projects, emails (full body), notes,\n"
            "file names, file content (PDF/Word/text), and daily tasks.\n\n"
            "Use 'Rebuild Index' in the Search tab to refresh the index\n"
            "after importing large amounts of data."
        )
        info_text.setStyleSheet("color: #a6adc8; font-size: 12px;")
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text)
        layout.addWidget(info_group)

        layout.addStretch()
        return w

    def _build_notifications_tab(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)

        self.notif_enabled_cb = QCheckBox("Enable Task Reminders")
        self.notif_enabled_cb.setChecked(self.settings.get("notifications_enabled", True))
        layout.addRow(self.notif_enabled_cb)

        self.lead_hours_spin = QSpinBox()
        self.lead_hours_spin.setRange(1, 72)
        self.lead_hours_spin.setValue(self.settings.get("reminder_lead_hours", 24))
        self.lead_hours_spin.setSuffix(" hours")
        layout.addRow("Reminder Lead Time:", self.lead_hours_spin)

        return w

    def _build_paths_tab(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)

        self.base_folder_edit = QLineEdit()
        self.base_folder_edit.setText(self.settings.get("base_folder", ""))
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.base_folder_edit, 1)
        folder_row.addWidget(browse_btn)
        layout.addRow("Projects Folder:", folder_row)

        return w

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Projects Folder")
        if folder:
            self.base_folder_edit.setText(folder)

    def _save_and_close(self):
        self.settings["theme"] = self.theme_combo.currentText()
        self.settings["font_family"] = self.font_combo.currentFont().family()
        self.settings["font_size"] = self.font_size_slider.value()
        self.settings["dashboard_sort"] = self.sort_combo.currentText()
        self.settings["card_size"] = self.card_size_combo.currentText()
        self.settings["ai_enabled"] = self.ai_enabled_cb.isChecked()
        self.settings["ai_api_key"] = self.api_key_edit.text()
        self.settings["ai_model"] = self.model_edit.text()
        self.settings["ai_frequency"] = self.freq_combo.currentText()
        self.settings["ai_system_prompt"] = self.system_prompt_edit.toPlainText().strip()
        self.settings["ml_search_enabled"] = self.ml_search_cb.isChecked()
        self.settings["notifications_enabled"] = self.notif_enabled_cb.isChecked()
        self.settings["reminder_lead_hours"] = self.lead_hours_spin.value()
        self.settings["base_folder"] = self.base_folder_edit.text()

        save_settings(self.settings)
        self.settings_changed.emit(self.settings)
        self.accept()
