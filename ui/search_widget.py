"""Global Search tab widget for ShipLog.

Provides a Google-like search interface with three modes:
  - Smart Search (FTS5 BM25 ranked)
  - Exact Keyword (SQL LIKE)
  - Semantic ML (TF-IDF cosine similarity, optional)

Results are clickable and navigate to the source project/email/note/file.
"""

import html as html_mod
import logging
import re

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QRadioButton, QButtonGroup, QFrame,
    QScrollArea, QProgressBar,
)

from shiplog.core.search_engine import (
    SearchResult, SearchWorker, IndexWorker, TfidfSearchEngine,
    TfidfBuildWorker,
)

logger = logging.getLogger(__name__)

# Type icons for results
TYPE_ICONS = {
    "project": "P",
    "email": "E",
    "note": "N",
    "file": "F",
    "file_content": "F",
    "task": "T",
}
TYPE_LABELS = {
    "project": "Project",
    "email": "Email",
    "note": "Note",
    "file": "File",
    "file_content": "File Content",
    "task": "Daily Task",
}
TYPE_COLORS = {
    "project": "#89b4fa",
    "email": "#f9e2af",
    "note": "#a6e3a1",
    "file": "#fab387",
    "file_content": "#fab387",
    "task": "#cba6f7",
}


class SearchResultCard(QFrame):
    """Clickable card displaying a single search result."""
    clicked = pyqtSignal(object)  # emits the SearchResult

    def __init__(self, result: SearchResult, query: str = "", parent=None):
        super().__init__(parent)
        self.result = result
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("card", True)
        self.setStyleSheet(
            "QFrame { border: 1px solid #45475a; border-radius: 8px; "
            "padding: 10px; margin: 2px 4px; }"
            "QFrame:hover { border-color: #89b4fa; }"
        )
        self._build_ui(query)

    def _build_ui(self, query: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # Top row: type icon + title
        top_row = QHBoxLayout()

        # Type badge
        color = TYPE_COLORS.get(self.result.source_type, "#cdd6f4")
        icon_text = TYPE_ICONS.get(self.result.source_type, "?")
        type_label = QLabel(icon_text)
        type_label.setFixedSize(24, 24)
        type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        type_label.setStyleSheet(
            f"background-color: {color}; color: #1e1e2e; "
            f"border-radius: 4px; font-weight: bold; font-size: 11px;"
        )
        top_row.addWidget(type_label)

        # Type name
        type_name = QLabel(TYPE_LABELS.get(self.result.source_type, "Unknown"))
        type_name.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        top_row.addWidget(type_name)

        top_row.addStretch()

        # Score badge
        if self.result.score > 0:
            score_text = f"{self.result.score:.2f}"
            score_label = QLabel(score_text)
            score_label.setStyleSheet(
                "background-color: #a6e3a1; color: #1e1e2e; "
                "border-radius: 4px; padding: 2px 6px; font-size: 10px; "
                "font-weight: bold;"
            )
            top_row.addWidget(score_label)

        layout.addLayout(top_row)

        # Title
        title_text = self.result.title or "(untitled)"
        if len(title_text) > 120:
            title_text = title_text[:120] + "..."
        title_label = QLabel(html_mod.escape(title_text))
        title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        # Project context
        if self.result.project_title and self.result.source_type != "project":
            ctx = QLabel(f"in Project: {html_mod.escape(self.result.project_title)}")
            ctx.setStyleSheet("color: #6c7086; font-size: 11px; font-style: italic;")
            layout.addWidget(ctx)

        # Snippet with highlights
        if self.result.snippet:
            snippet_text = self.result.snippet
            # If snippet has <mark> tags (from FTS5), render as HTML
            if "<mark>" in snippet_text:
                snippet_html = snippet_text.replace(
                    "<mark>", '<span style="background-color: #f9e2af; color: #1e1e2e; '
                              'padding: 1px 2px; border-radius: 2px;">'
                ).replace("</mark>", "</span>")
            else:
                # Manually highlight query terms
                snippet_html = html_mod.escape(snippet_text)
                if query:
                    for term in query.split():
                        if term:
                            pattern = re.compile(re.escape(term), re.IGNORECASE)
                            snippet_html = pattern.sub(
                                lambda m: (
                                    f'<span style="background-color: #f9e2af; '
                                    f'color: #1e1e2e; padding: 1px 2px; '
                                    f'border-radius: 2px;">{m.group()}</span>'
                                ),
                                snippet_html
                            )

            snippet_label = QLabel(snippet_html)
            snippet_label.setTextFormat(Qt.TextFormat.RichText)
            snippet_label.setWordWrap(True)
            snippet_label.setStyleSheet("font-size: 12px; color: #a6adc8;")
            layout.addWidget(snippet_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.result)
        super().mousePressEvent(event)


class SearchWidget(QWidget):
    """Main search tab widget with Google-like search interface."""

    # Navigation signals
    navigate_to_project = pyqtSignal(int)
    navigate_to_project_item = pyqtSignal(int, int, str, str)  # pid, item_id, type, query
    search_completed = pyqtSignal()

    def __init__(self, db, file_manager=None, settings: dict = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.fm = file_manager
        self.settings = settings or {}
        self._search_worker = None
        self._index_worker = None
        self._tfidf_worker = None
        self._tfidf_engine = None  # shared TF-IDF engine instance
        self._last_query = ""
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 10)

        # ── Search header ──
        header = QLabel("Search")
        header.setStyleSheet("font-size: 22px; font-weight: bold; color: #89b4fa;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(header)

        main_layout.addSpacing(10)

        # ── Search bar row ──
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search projects, emails, notes, files...")
        self.search_input.setMinimumHeight(40)
        self.search_input.setStyleSheet(
            "font-size: 15px; padding: 8px 14px; border-radius: 20px;"
        )
        self.search_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self.search_input, 1)

        self.search_btn = QPushButton("Search")
        self.search_btn.setMinimumHeight(40)
        self.search_btn.setFixedWidth(100)
        self.search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self.search_btn)
        main_layout.addLayout(search_row)

        # ── Mode selection row ──
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))

        self.mode_group = QButtonGroup(self)
        self.smart_radio = QRadioButton("Smart Search")
        self.smart_radio.setChecked(True)
        self.smart_radio.setToolTip("FTS5 full-text search with stemming and BM25 ranking")
        self.mode_group.addButton(self.smart_radio)
        mode_row.addWidget(self.smart_radio)

        self.keyword_radio = QRadioButton("Exact Keyword")
        self.keyword_radio.setToolTip("Simple substring matching across all content")
        self.mode_group.addButton(self.keyword_radio)
        mode_row.addWidget(self.keyword_radio)

        self.semantic_radio = QRadioButton("Semantic (ML)")
        self.semantic_radio.setToolTip(
            "TF-IDF contextual search. Enable in Settings."
        )
        self.mode_group.addButton(self.semantic_radio)
        mode_row.addWidget(self.semantic_radio)

        mode_row.addStretch()
        main_layout.addLayout(mode_row)

        # ── Index controls row ──
        idx_row = QHBoxLayout()
        self.reindex_btn = QPushButton("Rebuild Index")
        self.reindex_btn.setFixedWidth(130)
        self.reindex_btn.clicked.connect(self._on_reindex)
        idx_row.addWidget(self.reindex_btn)

        self.build_ml_btn = QPushButton("Build ML Index")
        self.build_ml_btn.setFixedWidth(130)
        self.build_ml_btn.clicked.connect(self._on_build_tfidf)
        self.build_ml_btn.setVisible(False)
        idx_row.addWidget(self.build_ml_btn)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #6c7086; font-size: 12px;")
        idx_row.addWidget(self.status_label, 1)

        self.result_count_label = QLabel("")
        self.result_count_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        idx_row.addWidget(self.result_count_label)
        main_layout.addLayout(idx_row)

        # ── Progress bar ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumHeight(6)
        self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

        # ── Results area ──
        self.results_area = QScrollArea()
        self.results_area.setWidgetResizable(True)
        self.results_content = QWidget()
        self.results_layout = QVBoxLayout(self.results_content)
        self.results_layout.setSpacing(4)
        self.results_layout.addStretch()
        self.results_area.setWidget(self.results_content)
        main_layout.addWidget(self.results_area, 1)

        # Now that all widgets exist, set ML visibility
        self._update_ml_visibility()

    def update_settings(self, settings: dict):
        """Update settings and refresh ML visibility."""
        self.settings = settings
        self._update_ml_visibility()

    def _update_ml_visibility(self):
        """Show/hide ML search option based on settings."""
        ml_enabled = self.settings.get("ml_search_enabled", False)
        ml_available = TfidfSearchEngine.is_available()
        show_ml = ml_enabled and ml_available
        self.semantic_radio.setVisible(show_ml)
        self.build_ml_btn.setVisible(show_ml)
        if not show_ml and self.semantic_radio.isChecked():
            self.smart_radio.setChecked(True)

    def _get_search_mode(self) -> str:
        if self.smart_radio.isChecked():
            return "smart"
        elif self.keyword_radio.isChecked():
            return "keyword"
        elif self.semantic_radio.isChecked():
            return "semantic"
        return "smart"

    # ── Search execution ─────────────────────────────────────

    def _on_search(self):
        query = self.search_input.text().strip()
        if not query:
            return
        if len(query) < 2:
            self.status_label.setText("Query too short (min 2 characters)")
            return

        # Check if index exists
        if self.db.search_index_count() == 0:
            self.status_label.setText("Index is empty. Click 'Rebuild Index' first.")
            return

        self._last_query = query
        self.search_btn.setEnabled(False)
        self.status_label.setText("Searching...")
        self.result_count_label.setText("")

        mode = self._get_search_mode()
        self._search_worker = SearchWorker(
            self.db, query, mode,
            tfidf_engine=self._tfidf_engine,
            parent=self,
        )
        self._search_worker.results_ready.connect(self._on_results_ready)
        self._search_worker.error_occurred.connect(self._on_search_error)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_results_ready(self, results: list, query: str):
        self._clear_results()
        if not results:
            no_result = QLabel("No results found.")
            no_result.setStyleSheet(
                "color: #6c7086; font-size: 14px; padding: 20px;"
            )
            no_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.results_layout.insertWidget(
                self.results_layout.count() - 1, no_result
            )
            self.result_count_label.setText("0 results")
            return

        self.result_count_label.setText(f"{len(results)} results")

        for result in results:
            card = SearchResultCard(result, query, parent=self)
            card.clicked.connect(self._on_result_clicked)
            self.results_layout.insertWidget(
                self.results_layout.count() - 1, card
            )

    def _on_search_error(self, error: str):
        self.status_label.setText(f"Search error: {error}")
        self.status_label.setStyleSheet("color: #f38ba8; font-size: 12px;")

    def _on_search_finished(self):
        self.search_btn.setEnabled(True)
        self.status_label.setText("Ready")
        self.status_label.setStyleSheet("color: #6c7086; font-size: 12px;")
        self.search_completed.emit()

    def _on_result_clicked(self, result: SearchResult):
        """Navigate to the clicked result."""
        if result.source_type == "project":
            self.navigate_to_project.emit(result.project_id)
        elif result.source_type in ("email", "note", "file", "file_content"):
            stype = result.source_type
            if stype == "file_content":
                stype = "file"
            self.navigate_to_project_item.emit(
                result.project_id, result.source_id, stype, self._last_query
            )
        elif result.source_type == "task":
            pass  # Tasks don't have a project to navigate to

    # ── Index controls ───────────────────────────────────────

    def _on_reindex(self):
        if self._index_worker and self._index_worker.isRunning():
            return
        self.reindex_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.status_label.setText("Building index...")

        self._index_worker = IndexWorker(self.db, self.fm, parent=self)
        self._index_worker.status_message.connect(
            lambda msg: self.status_label.setText(msg)
        )
        self._index_worker.progress.connect(self._on_index_progress)
        self._index_worker.finished_indexing.connect(self._on_index_finished)
        self._index_worker.error_occurred.connect(self._on_index_error)
        self._index_worker.start()

    def _on_index_progress(self, current: int, total: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)

    def _on_index_finished(self, total: int):
        self.reindex_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Index ready: {total} items indexed")
        self.status_label.setStyleSheet("color: #a6e3a1; font-size: 12px;")
        QTimer.singleShot(
            3000,
            lambda: self.status_label.setStyleSheet("color: #6c7086; font-size: 12px;")
        )

    def _on_index_error(self, error: str):
        self.reindex_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Index error: {error}")
        self.status_label.setStyleSheet("color: #f38ba8; font-size: 12px;")

    # ── TF-IDF ML controls ───────────────────────────────────

    def _on_build_tfidf(self):
        if self._tfidf_worker and self._tfidf_worker.isRunning():
            return
        self.build_ml_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("Building ML index...")

        self._tfidf_worker = TfidfBuildWorker(self.db, parent=self)
        self._tfidf_worker.status_message.connect(
            lambda msg: self.status_label.setText(msg)
        )
        self._tfidf_worker.finished_building.connect(self._on_tfidf_finished)
        self._tfidf_worker.error_occurred.connect(self._on_tfidf_error)
        self._tfidf_worker.start()

    def _on_tfidf_finished(self, engine_obj):
        self._tfidf_engine = engine_obj
        self.build_ml_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("ML index ready")
        self.status_label.setStyleSheet("color: #a6e3a1; font-size: 12px;")
        QTimer.singleShot(
            3000,
            lambda: self.status_label.setStyleSheet("color: #6c7086; font-size: 12px;")
        )

    def _on_tfidf_error(self, error: str):
        self.build_ml_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"ML index error: {error}")
        self.status_label.setStyleSheet("color: #f38ba8; font-size: 12px;")

    # ── Result management ────────────────────────────────────

    def _clear_results(self):
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def cleanup_workers(self):
        """Stop all background workers. Call from MainWindow.closeEvent."""
        for worker in [self._search_worker, self._index_worker, self._tfidf_worker]:
            if worker and worker.isRunning():
                worker.quit()
                worker.wait(2000)
