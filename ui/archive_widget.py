"""Archive widget with sub-tabs for Projects and Daily Tasks.

Provides bulk selection, restore, and delete operations for archived items.
Uses native list selection (Ctrl+click, Shift+click) instead of checkboxes.
"""

from datetime import date

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QTabWidget,
    QMessageBox, QAbstractItemView,
)


class ArchiveItemWidget(QWidget):
    """Row widget for an archived item."""

    def __init__(self, item_id: int, title: str, priority: str,
                 date_str: str, item_type: str, parent=None):
        super().__init__(parent)
        self.item_id = item_id
        self.item_type = item_type
        self.setMinimumHeight(38)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        name_label = QLabel(title)
        name_label.setStyleSheet("font-weight: bold;")
        name_label.setWordWrap(True)
        layout.addWidget(name_label, 1)

        priority_label = QLabel(priority)
        priority_label.setProperty("priority", priority)
        priority_label.setFixedWidth(70)
        priority_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(priority_label)

        if date_str:
            date_label = QLabel(date_str)
            date_label.setFixedWidth(100)
            date_label.setStyleSheet("font-size: 12px; color: #6c7086;")
            layout.addWidget(date_label)


class ArchiveWidget(QWidget):
    """Archive tab with sub-tabs for Projects and Daily Tasks, plus bulk management."""

    project_open = pyqtSignal(int)
    archive_changed = pyqtSignal()

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db = database
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QHBoxLayout()
        title = QLabel("Archive")
        title.setProperty("heading", True)
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        # Toolbar for bulk actions
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self._on_select_all)
        toolbar.addWidget(self.select_all_btn)

        self.selection_label = QLabel("0 items selected")
        self.selection_label.setStyleSheet("font-size: 12px; color: #6c7086;")
        toolbar.addWidget(self.selection_label)

        toolbar.addStretch()

        self.restore_btn = QPushButton("Restore Selected")
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self._restore_selected)
        toolbar.addWidget(self.restore_btn)

        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.setStyleSheet("background-color: #f38ba8;")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_selected)
        toolbar.addWidget(self.delete_btn)

        layout.addLayout(toolbar)

        # Sub-tabs
        self.sub_tabs = QTabWidget()
        self.sub_tabs.currentChanged.connect(self._on_sub_tab_changed)

        # Projects sub-tab
        self.projects_list = QListWidget()
        self.projects_list.setAlternatingRowColors(True)
        self.projects_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.projects_list.itemDoubleClicked.connect(self._on_project_double_clicked)
        self.projects_list.itemSelectionChanged.connect(self._update_selection_count)
        self.sub_tabs.addTab(self.projects_list, "Projects")

        # Daily Tasks sub-tab
        self.tasks_list = QListWidget()
        self.tasks_list.setAlternatingRowColors(True)
        self.tasks_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.tasks_list.itemSelectionChanged.connect(self._update_selection_count)
        self.sub_tabs.addTab(self.tasks_list, "Daily Tasks")

        layout.addWidget(self.sub_tabs, 1)

    def refresh(self):
        """Reload archived items from database."""
        self._load_projects()
        self._load_tasks()
        self._update_selection_count()

    def _load_projects(self):
        self.projects_list.clear()
        projects = self.db.get_all_projects(status="Archived")

        if not projects:
            item = QListWidgetItem("No archived projects.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.projects_list.addItem(item)
            return

        for project in projects:
            date_str = ""
            if project.get("closed_at"):
                date_str = project["closed_at"][:10]
            widget = ArchiveItemWidget(
                project["id"], project["title"], project["priority"],
                date_str, "project"
            )
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 42))
            item.setData(Qt.ItemDataRole.UserRole, project["id"])
            self.projects_list.addItem(item)
            self.projects_list.setItemWidget(item, widget)

    def _load_tasks(self):
        self.tasks_list.clear()
        tasks = self.db.get_archived_tasks()

        if not tasks:
            item = QListWidgetItem("No archived daily tasks.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tasks_list.addItem(item)
            return

        for task in tasks:
            date_str = task.get("last_completed") or ""
            # Show linked project name if available
            title = task["name"]
            if task.get("project_id"):
                project = self.db.get_project(task["project_id"])
                if project:
                    title = f"{task['name']}  [{project['title']}]"
            widget = ArchiveItemWidget(
                task["id"], title, task["priority"],
                date_str, "task"
            )
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 42))
            item.setData(Qt.ItemDataRole.UserRole, task["id"])
            self.tasks_list.addItem(item)
            self.tasks_list.setItemWidget(item, widget)

    def _get_active_list(self) -> QListWidget:
        """Return the currently active sub-tab's list widget."""
        if self.sub_tabs.currentIndex() == 0:
            return self.projects_list
        return self.tasks_list

    def _get_selected_items(self) -> list[ArchiveItemWidget]:
        """Get all selected ArchiveItemWidgets from the active list."""
        active_list = self._get_active_list()
        selected = []
        for item in active_list.selectedItems():
            widget = active_list.itemWidget(item)
            if isinstance(widget, ArchiveItemWidget):
                selected.append(widget)
        return selected

    def _update_selection_count(self):
        """Update the selection count label and button states."""
        selected = self._get_selected_items()
        count = len(selected)
        self.selection_label.setText(f"{count} item{'s' if count != 1 else ''} selected")
        self.restore_btn.setEnabled(count > 0)
        self.delete_btn.setEnabled(count > 0)

    def _on_select_all(self):
        """Select all items on the active sub-tab."""
        active_list = self._get_active_list()
        active_list.selectAll()

    def _on_sub_tab_changed(self, index: int):
        """Reset selection state when switching sub-tabs."""
        self._update_selection_count()

    def _on_project_double_clicked(self, item: QListWidgetItem):
        """Open project detail on double-click."""
        project_id = item.data(Qt.ItemDataRole.UserRole)
        if project_id:
            self.project_open.emit(project_id)

    def _restore_selected(self):
        """Restore all selected items."""
        selected = self._get_selected_items()
        if not selected:
            return

        item_type = "projects" if self.sub_tabs.currentIndex() == 0 else "tasks"
        reply = QMessageBox.question(
            self, "Restore Items",
            f"Restore {len(selected)} archived {item_type}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for widget in selected:
            if widget.item_type == "project":
                self.db.restore_project(widget.item_id)
            else:
                self.db.restore_task(widget.item_id)

        self.archive_changed.emit()

    def _delete_selected(self):
        """Permanently delete all selected items."""
        selected = self._get_selected_items()
        if not selected:
            return

        item_type = "projects" if self.sub_tabs.currentIndex() == 0 else "tasks"
        reply = QMessageBox.question(
            self, "Delete Items",
            f"Permanently delete {len(selected)} {item_type}?\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for widget in selected:
            if widget.item_type == "project":
                self.db.delete_project(widget.item_id)
            else:
                self.db.delete_task(widget.item_id)

        self.archive_changed.emit()
