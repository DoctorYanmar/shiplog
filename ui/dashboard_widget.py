"""Dashboard widget showing all active projects in a sortable card view."""

from datetime import date

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QLineEdit, QComboBox, QPushButton, QSizePolicy,
)


class ProjectCard(QFrame):
    """A single project card for the dashboard."""

    clicked = pyqtSignal(int)  # project_id
    view_summary = pyqtSignal(int)  # project_id

    def __init__(self, project: dict, parent=None):
        super().__init__(parent)
        self.project_id = project["id"]
        self.setProperty("card", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._build_ui(project)

    def _build_ui(self, project: dict):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Top row: title + priority badge
        top = QHBoxLayout()
        title = QLabel(project["title"])
        title.setProperty("heading", True)
        title.setWordWrap(True)
        top.addWidget(title, 1)

        priority_label = QLabel(project["priority"])
        priority_label.setProperty("priority", project["priority"])
        priority_label.setFixedHeight(24)
        priority_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(priority_label)
        layout.addLayout(top)

        # Deadline
        if project.get("deadline"):
            dl_text = f"Deadline: {project['deadline']}"
            try:
                dl_date = date.fromisoformat(project["deadline"])
                if dl_date < date.today():
                    dl_text += "  [OVERDUE]"
            except (ValueError, TypeError):
                pass
            dl_label = QLabel(dl_text)
            dl_label.setStyleSheet("font-size: 12px; color: #f38ba8;" if "OVERDUE" in dl_text else "font-size: 12px;")
            layout.addWidget(dl_label)

        # AI summary — short inline + view button
        ai_raw = project.get("ai_summary", "")
        if ai_raw:
            ai_row = QHBoxLayout()
            if "---FULL---" in ai_raw:
                short_text = ai_raw.split("---FULL---", 1)[0].strip()
            else:
                short_text = ai_raw[:120] + ("..." if len(ai_raw) > 120 else "")
            ai_label = QLabel(short_text)
            ai_label.setWordWrap(True)
            ai_label.setStyleSheet("font-size: 12px; font-style: italic;")
            ai_row.addWidget(ai_label, 1)

            view_btn = QPushButton("AI")
            view_btn.setFixedSize(32, 24)
            view_btn.setStyleSheet("font-size: 11px; padding: 2px;")
            view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            view_btn.clicked.connect(lambda: self.view_summary.emit(self.project_id))
            ai_row.addWidget(view_btn)
            layout.addLayout(ai_row)

        # What's needed snippet
        if project.get("whats_needed"):
            needed = QLabel(f"Needed: {project['whats_needed'][:120]}")
            needed.setWordWrap(True)
            needed.setStyleSheet("font-size: 12px;")
            layout.addWidget(needed)

        # Bottom: last modified
        if project.get("last_modified_at"):
            modified = QLabel(f"Modified: {project['last_modified_at'][:16]}")
            modified.setStyleSheet("font-size: 11px; color: #6c7086;")
            modified.setAlignment(Qt.AlignmentFlag.AlignRight)
            layout.addWidget(modified)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.project_id)
        super().mousePressEvent(event)


class DashboardWidget(QWidget):
    """Main dashboard showing active project cards."""

    project_open = pyqtSignal(int)       # project_id
    project_create = pyqtSignal()
    summary_view = pyqtSignal(int)       # project_id — open AI summary popup

    def __init__(self, database, status_filter: str = "Active", parent=None):
        super().__init__(parent)
        self.db = database
        self.status_filter = status_filter
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QHBoxLayout()
        label_text = "Archived Projects" if self.status_filter == "Archived" else "Active Projects"
        title = QLabel(label_text)
        title.setProperty("heading", True)
        header.addWidget(title)
        header.addStretch()

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search projects...")
        self.search_box.setMaximumWidth(250)
        self.search_box.textChanged.connect(self._on_search)
        header.addWidget(self.search_box)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Priority + Deadline", "Last Modified", "Title A-Z"])
        self.sort_combo.currentIndexChanged.connect(lambda _: self.refresh())
        header.addWidget(self.sort_combo)

        new_btn = QPushButton("+ New Project")
        new_btn.clicked.connect(self.project_create.emit)
        header.addWidget(new_btn)

        layout.addLayout(header)

        # Scroll area for cards
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_content = QWidget()
        self.cards_layout = QVBoxLayout(self.scroll_content)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll)

    def refresh(self, search_query: str = ""):
        """Reload projects from database and rebuild cards."""
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if search_query:
            projects = self.db.search_projects(search_query, status=self.status_filter)
        else:
            projects = self.db.get_all_projects(status=self.status_filter)

        sort_idx = self.sort_combo.currentIndex()
        if sort_idx == 1:
            projects.sort(key=lambda p: p.get("last_modified_at", ""), reverse=True)
        elif sort_idx == 2:
            projects.sort(key=lambda p: p.get("title", "").lower())

        if not projects:
            empty_label = QLabel("No active projects. Click '+ New Project' to get started.")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setStyleSheet("font-size: 16px; padding: 40px; color: #6c7086;")
            self.cards_layout.insertWidget(0, empty_label)
        else:
            for project in projects:
                card = ProjectCard(project)
                card.clicked.connect(self.project_open.emit)
                card.view_summary.connect(self.summary_view.emit)
                self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

    def _on_search(self, text: str):
        self.refresh(search_query=text)
