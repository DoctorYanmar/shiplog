"""Main application window: Dashboard + Tasks + Archive + Settings.

Uses QStackedWidget for view switching and QSettings for geometry persistence.
"""

import hashlib
import logging
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QTimer
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QMainWindow, QStackedWidget, QTabWidget, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QStatusBar,
    QMessageBox, QSystemTrayIcon, QMenu, QApplication,
)

from shiplog.core.database import Database
from shiplog.core.file_manager import FileManager
from shiplog.core.ai_service import (
    AISummaryWorker, AIDigestWorker, check_internet, DEFAULT_SYSTEM_PROMPT,
)
from shiplog.core.scheduler import TaskScheduler
from shiplog.ui.dashboard_widget import DashboardWidget
from shiplog.ui.task_widget import TaskWidget
from shiplog.ui.project_window import ProjectWindow, ProjectCreateDialog, AISummaryDialog
from shiplog.ui.settings_dialog import SettingsDialog, load_settings, save_settings
from shiplog.ui.search_widget import SearchWidget

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """ShipLog main application window."""

    def __init__(self, db: Database, settings: dict):
        super().__init__()
        self.db = db
        self.settings = settings
        self.fm = FileManager(settings.get("base_folder"))
        self.ai_workers = []
        self._current_project_window = None
        self.tray = None
        self._summary_queue = []       # list of project IDs awaiting summarization
        self._summary_total = 0        # total projects in current batch
        self._summary_running = False  # True while queue is being processed

        self.setWindowTitle("ShipLog — Marine Project Manager")
        self.setMinimumSize(900, 600)

        self._restore_geometry()
        self._build_ui()
        self._build_menu()
        self._build_status_bar()
        self._setup_tray()
        self._setup_scheduler()

        # Initial load
        self.dashboard.refresh()
        self.task_widget.refresh()
        self.archive_widget.refresh()

        # Auto-build search index if empty (delayed to not block startup)
        QTimer.singleShot(500, self._auto_index_if_needed)

    def _build_ui(self):
        """Build the main UI with stacked widget and tabs."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Stacked widget: index 0 = tabs, index 1 = project detail
        self.stack = QStackedWidget()

        # ── Tab view (Dashboard, Tasks, Archive) ──
        self.tabs = QTabWidget()

        # Dashboard tab
        self.dashboard = DashboardWidget(self.db)
        self.dashboard.project_open.connect(self._open_project)
        self.dashboard.project_create.connect(self._create_project)
        self.dashboard.summary_view.connect(self._show_summary_popup)
        self.tabs.addTab(self.dashboard, "Dashboard")

        # Tasks tab
        self.task_widget = TaskWidget(self.db)
        self.task_widget.navigate_to_project.connect(self._open_project)
        self.tabs.addTab(self.task_widget, "Daily Tasks")

        # Search tab
        self.search_widget = SearchWidget(self.db, self.fm, self.settings)
        self.search_widget.navigate_to_project.connect(self._open_project)
        self.search_widget.navigate_to_project_item.connect(
            self._open_project_with_highlight
        )
        self.search_widget.search_completed.connect(self._on_search_completed)
        self.tabs.addTab(self.search_widget, "Search")

        # Archive tab
        from shiplog.ui.archive_widget import ArchiveWidget
        self.archive_widget = ArchiveWidget(self.db)
        self.archive_widget.project_open.connect(self._open_project)
        self.archive_widget.archive_changed.connect(self._on_archive_changed)
        self.tabs.addTab(self.archive_widget, "Archive")

        # Reset tab color when switching to Search tab
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.stack.addWidget(self.tabs)

        # Project detail placeholder (added dynamically)
        self.stack.addWidget(QWidget())

        layout.addWidget(self.stack)

    def _build_menu(self):
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("&File")

        new_project_action = QAction("&New Project", self)
        new_project_action.setShortcut("Ctrl+N")
        new_project_action.triggered.connect(self._create_project)
        file_menu.addAction(new_project_action)

        settings_action = QAction("&Settings", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # AI menu
        ai_menu = menu_bar.addMenu("&AI")

        summarize_all = QAction("Summarize All", self)
        summarize_all.triggered.connect(self._summarize_all_projects)
        ai_menu.addAction(summarize_all)

        digest_action = QAction("Weekly Digest", self)
        digest_action.triggered.connect(self._generate_digest)
        ai_menu.addAction(digest_action)

        # Help menu
        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About ShipLog", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _build_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.ai_status_label = QLabel("AI: disabled")
        self.status_bar.addPermanentWidget(self.ai_status_label)
        self._update_ai_status()

    def _update_ai_status(self):
        if not self.settings.get("ai_enabled"):
            self.ai_status_label.setText("AI: disabled")
        elif check_internet():
            self.ai_status_label.setText("AI: online")
        else:
            self.ai_status_label.setText("AI: offline")

    def _setup_scheduler(self):
        self.scheduler = TaskScheduler(self.db, parent=self)
        self.scheduler.tasks_due.connect(self._on_tasks_due)
        self.scheduler.tasks_upcoming.connect(self._on_tasks_upcoming)
        if self.settings.get("notifications_enabled", True):
            self.scheduler.start()

    def _setup_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self)
            tray_menu = QMenu()
            show_action = tray_menu.addAction("Show ShipLog")
            show_action.triggered.connect(self.show)
            quit_action = tray_menu.addAction("Quit")
            quit_action.triggered.connect(self.close)
            self.tray.setContextMenu(tray_menu)
            self.tray.setToolTip("ShipLog")
        else:
            self.tray = None

    # ── Project Operations ────────────────────────────────────

    def _create_project(self):
        dialog = ProjectCreateDialog(parent=self)
        if dialog.exec():
            data = dialog.get_data()
            if not data["title"]:
                return
            pid = self.db.create_project(
                title=data["title"],
                description=data["description"],
                priority=data["priority"],
                deadline=data["deadline"],
                whats_needed=data["whats_needed"],
            )
            self.fm.create_project_folder(pid, data["title"])
            self.dashboard.refresh()
            self._open_project(pid)

    def _open_project(self, project_id: int):
        project = self.db.get_project(project_id)
        if not project:
            QMessageBox.warning(self, "Error", "Project not found.")
            return

        # Check/recreate folder
        folder = self.fm.get_project_folder(project_id)
        if not folder or not folder.exists():
            reply = QMessageBox.question(
                self, "Missing Folder",
                f"Project folder for '{project['title']}' is missing.\n"
                "Re-create the folder structure?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.fm.create_project_folder(project_id, project["title"])

        pw = ProjectWindow(project_id, self.db, self.fm, self.settings)
        pw.project_updated.connect(self._on_project_updated)
        pw.back_requested.connect(self._back_to_dashboard)
        pw.navigate_to_project.connect(self._open_project)
        pw.refresh()

        # Replace the stack widget at index 1
        old = self.stack.widget(1)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.stack.addWidget(pw)
        self.stack.setCurrentIndex(1)
        self._current_project_window = pw

        # Trigger AI summary if enabled and frequency is on_open (not on_request)
        freq = self.settings.get("ai_frequency", "on_request")
        if self.settings.get("ai_enabled") and freq == "on_open":
            self._summarize_project(project_id)

    def _back_to_dashboard(self):
        self.stack.setCurrentIndex(0)
        self._current_project_window = None
        self.dashboard.refresh()
        self.archive_widget.refresh()
        # Save task splitter when returning to tabs
        self.task_widget.save_layout()

    def _on_project_updated(self):
        self.dashboard.refresh()
        self.archive_widget.refresh()

    def _on_archive_changed(self):
        """Refresh all views when items are restored/deleted from archive."""
        self.dashboard.refresh()
        self.task_widget.refresh()
        self.archive_widget.refresh()

    # ── AI Operations ─────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        return self.settings.get("ai_system_prompt", DEFAULT_SYSTEM_PROMPT)

    def _should_summarize(self, project: dict) -> bool:
        """Check if project needs AI re-summarization."""
        if not project.get("ai_summary"):
            return True
        summary_at = project.get("ai_summary_at")
        modified_at = project.get("last_modified_at")
        if not summary_at:
            return True
        if modified_at and modified_at > summary_at:
            return True
        # Check if system prompt changed since last batch
        current_hash = hashlib.md5(
            self._get_system_prompt().encode()
        ).hexdigest()
        qs = QSettings("ShipLog", "AI")
        stored_hash = qs.value("system_prompt_hash", "")
        if current_hash != stored_hash:
            return True
        return False

    def _summarize_project(self, project_id: int, force: bool = False):
        if not self.settings.get("ai_enabled"):
            return
        api_key = self.settings.get("ai_api_key", "")
        model = self.settings.get("ai_model", "")
        if not api_key or not model:
            return

        # Skip unchanged projects unless forced
        if not force:
            project = self.db.get_project(project_id)
            if project and not self._should_summarize(project):
                logger.info("Skipping unchanged project %d", project_id)
                return

        text = self.db.get_project_text_for_ai(project_id)
        if not text:
            return

        worker = AISummaryWorker(
            project_id, text, api_key, model,
            system_prompt=self._get_system_prompt(),
            parent=self,
        )
        worker.summary_ready.connect(self._on_summary_ready)
        worker.error_occurred.connect(self._on_ai_error)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self.ai_workers.append(worker)
        worker.start()
        self.status_bar.showMessage("AI: Generating summary...", 5000)

    def _summarize_all_projects(self):
        if not self.settings.get("ai_enabled"):
            QMessageBox.information(self, "AI Disabled",
                                    "Enable AI in Settings first.")
            return
        if self._summary_running:
            QMessageBox.information(self, "AI Busy",
                                    "Summarization is already in progress.")
            return

        # Save current prompt hash so we can detect future changes
        current_hash = hashlib.md5(
            self._get_system_prompt().encode()
        ).hexdigest()
        qs = QSettings("ShipLog", "AI")
        qs.setValue("system_prompt_hash", current_hash)

        # Build queue of project IDs that need summarization
        projects = self.db.get_all_projects("Active")
        queue = []
        for p in projects:
            proj = self.db.get_project(p["id"])
            if proj and self._should_summarize(proj):
                queue.append(p["id"])

        if not queue:
            self.status_bar.showMessage("AI: All projects are up to date.", 5000)
            return

        self._summary_queue = queue
        self._summary_total = len(queue)
        self._summary_running = True
        self.status_bar.showMessage(
            f"AI: Summarizing 1/{self._summary_total}...", 0
        )
        self._process_next_in_queue()

    def _process_next_in_queue(self):
        """Start summarization for the next project in the queue."""
        if not self._summary_queue:
            self._summary_running = False
            self.status_bar.showMessage(
                f"AI: Finished summarizing {self._summary_total} projects.", 5000
            )
            return

        project_id = self._summary_queue.pop(0)
        done = self._summary_total - len(self._summary_queue)
        self.status_bar.showMessage(
            f"AI: Summarizing {done}/{self._summary_total}...", 0
        )
        # Use force=True since we already filtered in _summarize_all_projects
        self._summarize_project(project_id, force=True)

    def _generate_digest(self):
        if not self.settings.get("ai_enabled"):
            QMessageBox.information(self, "AI Disabled",
                                    "Enable AI in Settings first.")
            return
        api_key = self.settings.get("ai_api_key", "")
        model = self.settings.get("ai_model", "")
        if not api_key or not model:
            QMessageBox.warning(self, "Missing Config",
                                "Set API key and model in Settings.")
            return

        projects = self.db.get_all_projects("Active")
        all_text = "\n\n---\n\n".join(
            self.db.get_project_text_for_ai(p["id"]) for p in projects
        )

        worker = AIDigestWorker(
            all_text, api_key, model,
            system_prompt=self._get_system_prompt(),
            parent=self,
        )
        worker.digest_ready.connect(self._on_digest_ready)
        worker.error_occurred.connect(
            lambda err: self.status_bar.showMessage(f"AI digest error: {err}", 5000)
        )
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self.ai_workers.append(worker)
        worker.start()
        self.status_bar.showMessage("AI: Generating weekly digest...", 10000)

    def _on_summary_ready(self, project_id: int, short_summary: str, full_summary: str):
        combined = f"{short_summary}\n---FULL---\n{full_summary}"
        self.db.update_project(
            project_id, ai_summary=combined,
            ai_summary_at=datetime.now().isoformat()
        )
        self.dashboard.refresh()
        if (self._current_project_window and
                self._current_project_window.project_id == project_id):
            self._current_project_window.update_ai_summary(short_summary, full_summary)
        if self._summary_running:
            self._process_next_in_queue()
        else:
            self.status_bar.showMessage("AI summary updated.", 3000)

    def _on_digest_ready(self, digest: str):
        self.db.save_digest(digest)
        dialog = AISummaryDialog("Weekly Digest", digest, parent=self)
        dialog.exec()
        self.status_bar.showMessage("Weekly digest generated.", 3000)

    def _on_ai_error(self, project_id: int, error: str):
        logger.warning("AI error for project %d: %s", project_id, error)
        if self._summary_running:
            # Continue with next project even if one fails
            self.status_bar.showMessage(
                f"AI error on project {project_id}: {error}", 3000
            )
            self._process_next_in_queue()
        else:
            self.status_bar.showMessage(f"AI error: {error}", 5000)

    def _cleanup_worker(self, worker):
        if worker in self.ai_workers:
            self.ai_workers.remove(worker)
        self._update_ai_status()

    def _show_summary_popup(self, project_id: int):
        """Show full AI summary in a popup (from dashboard card button)."""
        project = self.db.get_project(project_id)
        if not project:
            return
        ai_raw = project.get("ai_summary", "")
        if "---FULL---" in ai_raw:
            full_text = ai_raw.split("---FULL---", 1)[1].strip()
        else:
            full_text = ai_raw or "No AI summary available."
        dialog = AISummaryDialog(
            f"AI Summary — {project['title']}", full_text, parent=self
        )
        dialog.exec()

    # ── Task Notifications ────────────────────────────────────

    def _on_tasks_due(self, tasks: list):
        if not self.settings.get("notifications_enabled"):
            return
        names = ", ".join(t["name"] for t in tasks[:5])
        if self.tray:
            self.tray.showMessage(
                "ShipLog — Tasks Due",
                f"Due today: {names}",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def _on_tasks_upcoming(self, tasks: list):
        if not self.settings.get("notifications_enabled"):
            return
        names = ", ".join(t["name"] for t in tasks[:5])
        if self.tray:
            self.tray.showMessage(
                "ShipLog — Upcoming Tasks",
                f"Due tomorrow: {names}",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    # ── Search ────────────────────────────────────────────────

    def _focus_search(self):
        """Switch to Search tab and focus the search input."""
        self.stack.setCurrentIndex(0)
        search_idx = self.tabs.indexOf(self.search_widget)
        self.tabs.setCurrentIndex(search_idx)
        self.search_widget.search_input.setFocus()
        self.search_widget.search_input.selectAll()

    def _open_project_with_highlight(self, project_id: int, item_id: int,
                                     item_type: str, search_query: str):
        """Open project and highlight a specific item."""
        self._open_project(project_id)
        if self._current_project_window:
            self._current_project_window.highlight_item(
                item_type, item_id, search_query
            )

    def _on_search_completed(self):
        """Highlight Search tab when search completes and tab is not active."""
        search_idx = self.tabs.indexOf(self.search_widget)
        if self.tabs.currentIndex() != search_idx:
            self.tabs.tabBar().setTabTextColor(search_idx, QColor("#a6e3a1"))

    def _on_tab_changed(self, index: int):
        """Reset Search tab color when user switches to it."""
        search_idx = self.tabs.indexOf(self.search_widget)
        if index == search_idx:
            self.tabs.tabBar().setTabTextColor(search_idx, QColor())

    def _auto_index_if_needed(self):
        """Auto-build search index on first launch if empty."""
        if self.db.search_index_count() == 0:
            self.search_widget._on_reindex()

    # ── Settings ──────────────────────────────────────────────

    def _open_settings(self):
        dialog = SettingsDialog(self.settings, parent=self)
        dialog.settings_changed.connect(self._apply_settings)
        dialog.exec()

    def _apply_settings(self, new_settings: dict):
        old_theme = self.settings.get("theme")
        self.settings = new_settings

        # Apply theme if changed
        if new_settings.get("theme") != old_theme:
            self._apply_theme(new_settings["theme"])

        # Update file manager base path
        if new_settings.get("base_folder"):
            self.fm = FileManager(new_settings["base_folder"])

        # Update scheduler
        if new_settings.get("notifications_enabled"):
            self.scheduler.start()
        else:
            self.scheduler.stop()

        self._update_ai_status()
        self.dashboard.refresh()

        # Update search widget with new settings
        self.search_widget.update_settings(new_settings)

    def _apply_theme(self, theme_name: str):
        theme_dir = Path(__file__).parent.parent / "assets" / "themes"
        theme_file = theme_dir / f"{theme_name}.qss"
        if theme_file.exists():
            with open(theme_file, "r") as f:
                qss = f.read()
            font_size = self.settings.get("font_size", 14)
            qss = qss.replace("font-size: 14px;", f"font-size: {font_size}px;")
            QApplication.instance().setStyleSheet(qss)
            logger.info("Applied theme: %s", theme_name)

    # ── About ─────────────────────────────────────────────────

    def _show_about(self):
        QMessageBox.about(
            self, "About ShipLog",
            "ShipLog v1.0\n\n"
            "Marine Project & Daily Task Manager\n"
            "for Ship Chief Engineers.\n\n"
            "Track technical problems, maintenance projects,\n"
            "and daily recurring tasks with AI-powered summaries."
        )

    # ── Window geometry persistence ───────────────────────────

    def _restore_geometry(self):
        qs = QSettings("ShipLog", "MainWindow")
        geometry = qs.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        state = qs.value("windowState")
        if state:
            self.restoreState(state)

    def closeEvent(self, event):
        qs = QSettings("ShipLog", "MainWindow")
        qs.setValue("geometry", self.saveGeometry())
        qs.setValue("windowState", self.saveState())

        # Save layout state
        if self._current_project_window:
            self._current_project_window.save_layout()
        self.task_widget.save_layout()

        # Stop scheduler
        self.scheduler.stop()

        # Wait for AI workers
        for worker in self.ai_workers:
            worker.quit()
            worker.wait(2000)

        # Cleanup search workers
        self.search_widget.cleanup_workers()

        super().closeEvent(event)
