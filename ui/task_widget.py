"""Daily micro-tasks widget with recurring task management.

Uses QSplitter between Due Today and All Tasks sections with persistent sizes.
"""

from datetime import date, timedelta

from PyQt6.QtCore import Qt, QSize, QDate, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QLineEdit, QComboBox,
    QDialog, QFormLayout, QDialogButtonBox, QFrame, QCheckBox,
    QMessageBox, QDateEdit, QSplitter,
)


class TaskItemWidget(QWidget):
    """Custom widget displayed in each list item row."""

    completed = pyqtSignal(int)  # task_id
    navigate_to_project = pyqtSignal(int)  # project_id

    def __init__(self, task: dict, project_name: str = "", parent=None):
        super().__init__(parent)
        self.task_id = task["id"]
        self._project_id = task.get("project_id")
        self.setMinimumHeight(38)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        # Done checkbox — fixed size for reliable click area
        self.done_cb = QCheckBox()
        self.done_cb.setFixedSize(24, 24)
        self.done_cb.toggled.connect(self._on_done)
        layout.addWidget(self.done_cb)

        # Name
        name_label = QLabel(task["name"])
        name_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(name_label, 1)

        # Project tag (clickable chip)
        if project_name and self._project_id:
            display = project_name[:20] + ("..." if len(project_name) > 20 else "")
            project_tag = QPushButton(display)
            project_tag.setProperty("projectTag", True)
            project_tag.setStyleSheet(
                "background: #313244; border-radius: 4px; padding: 2px 6px; "
                "font-size: 11px; color: #89b4fa; border: 1px solid #45475a;"
            )
            project_tag.setCursor(Qt.CursorShape.PointingHandCursor)
            project_tag.setFixedHeight(22)
            project_tag.clicked.connect(
                lambda: self.navigate_to_project.emit(self._project_id)
            )
            layout.addWidget(project_tag)

        # Priority badge
        priority_label = QLabel(task["priority"])
        priority_label.setProperty("priority", task["priority"])
        priority_label.setFixedWidth(70)
        priority_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(priority_label)

        # Recurrence
        rec_text = task["recurrence"].capitalize()
        if task["recurrence"] == "once":
            rec_text = "One-time"
        rec_label = QLabel(rec_text)
        rec_label.setFixedWidth(80)
        rec_label.setStyleSheet("font-size: 12px; color: #6c7086;")
        layout.addWidget(rec_label)

        # Deadline (shown only if set)
        deadline_str = task.get("deadline") or ""
        if deadline_str:
            dl_label = QLabel(f"DL: {deadline_str}")
            dl_label.setFixedWidth(95)
            dl_label.setStyleSheet("font-size: 11px; color: #fab387;")
            try:
                dl_date = date.fromisoformat(deadline_str)
                if dl_date < date.today():
                    dl_label.setStyleSheet(
                        "font-size: 11px; color: #f38ba8; font-weight: bold;"
                    )
            except (ValueError, TypeError):
                pass
            layout.addWidget(dl_label)

        # Due date
        due_label = QLabel(f"Due: {task['next_due'] or 'N/A'}")
        due_label.setFixedWidth(110)
        due_label.setStyleSheet("font-size: 12px;")
        if task.get("next_due"):
            try:
                due_date = date.fromisoformat(task["next_due"])
                if due_date <= date.today():
                    due_label.setStyleSheet(
                        "font-size: 12px; color: #f38ba8; font-weight: bold;"
                    )
            except (ValueError, TypeError):
                pass
        layout.addWidget(due_label)

    def _on_done(self, checked):
        if checked:
            self.completed.emit(self.task_id)


class TaskDialog(QDialog):
    """Dialog for creating/editing a daily task."""

    def __init__(self, task: dict = None, projects: list = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Task" if task else "New Task")
        self.setMinimumWidth(380)
        self._projects = projects or []

        layout = QFormLayout(self)

        self.name_edit = QLineEdit()
        if task:
            self.name_edit.setText(task.get("name", ""))
        layout.addRow("Task Name:", self.name_edit)

        self.priority_combo = QComboBox()
        self.priority_combo.addItems(["Low", "Medium", "High", "Critical"])
        if task:
            idx = self.priority_combo.findText(task.get("priority", "Medium"))
            if idx >= 0:
                self.priority_combo.setCurrentIndex(idx)
        else:
            self.priority_combo.setCurrentIndex(1)
        layout.addRow("Priority:", self.priority_combo)

        self.recurrence_combo = QComboBox()
        self.recurrence_combo.addItems([
            "once", "daily", "weekly", "monthly",
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday"
        ])
        if task:
            idx = self.recurrence_combo.findText(task.get("recurrence", "daily"))
            if idx >= 0:
                self.recurrence_combo.setCurrentIndex(idx)
        else:
            self.recurrence_combo.setCurrentIndex(1)  # default to "daily"
        layout.addRow("Recurrence:", self.recurrence_combo)

        # Deadline with checkbox toggle — calendar shows current date
        self.has_deadline_cb = QCheckBox("Set deadline")
        self.deadline_edit = QDateEdit()
        self.deadline_edit.setCalendarPopup(True)
        self.deadline_edit.setDate(QDate.currentDate())
        self.deadline_edit.setEnabled(False)
        self.has_deadline_cb.toggled.connect(self.deadline_edit.setEnabled)
        # Widen calendar popup so month name doesn't overlap year
        cal = self.deadline_edit.calendarWidget()
        cal.setMinimumWidth(350)
        if task and task.get("deadline"):
            try:
                d = QDate.fromString(task["deadline"], "yyyy-MM-dd")
                if d.isValid():
                    self.has_deadline_cb.setChecked(True)
                    self.deadline_edit.setDate(d)
            except Exception:
                pass
        dl_row = QHBoxLayout()
        dl_row.addWidget(self.has_deadline_cb)
        dl_row.addWidget(self.deadline_edit, 1)
        layout.addRow("Deadline:", dl_row)

        # Project linking
        self.project_combo = QComboBox()
        self.project_combo.addItem("(No Project)", None)
        for p in self._projects:
            self.project_combo.addItem(p["title"], p["id"])
        if task and task.get("project_id"):
            idx = self.project_combo.findData(task["project_id"])
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        layout.addRow("Project:", self.project_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self) -> dict:
        deadline = None
        if self.has_deadline_cb.isChecked():
            deadline = self.deadline_edit.date().toString("yyyy-MM-dd")
        return {
            "name": self.name_edit.text().strip(),
            "priority": self.priority_combo.currentText(),
            "recurrence": self.recurrence_combo.currentText(),
            "deadline": deadline,
            "project_id": self.project_combo.currentData(),
        }


class TaskWidget(QWidget):
    """Widget for managing daily recurring tasks."""

    navigate_to_project = pyqtSignal(int)  # project_id

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db = database
        self._project_filter_id = None
        self._build_ui()
        self._restore_layout()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QHBoxLayout()
        title = QLabel("Daily Tasks")
        title.setProperty("heading", True)
        header.addWidget(title)
        header.addStretch()

        # Project filter
        self.project_filter = QComboBox()
        self.project_filter.setMinimumWidth(150)
        self.project_filter.setMaximumWidth(250)
        self.project_filter.currentIndexChanged.connect(self._on_project_filter_changed)
        header.addWidget(self.project_filter)

        add_btn = QPushButton("+ New Task")
        add_btn.clicked.connect(self._add_task)
        header.addWidget(add_btn)
        layout.addLayout(header)

        # Toolbar
        toolbar = QHBoxLayout()
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self._edit_selected_task)
        toolbar.addWidget(self.edit_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Splitter between Due Today and All Tasks
        self.tasks_splitter = QSplitter(Qt.Orientation.Vertical)

        # Today's tasks container
        today_container = QWidget()
        today_layout = QVBoxLayout(today_container)
        today_layout.setContentsMargins(0, 0, 0, 0)
        today_header = QLabel("Due Today / Overdue")
        today_header.setStyleSheet("font-weight: bold; font-size: 15px; margin-top: 8px;")
        today_layout.addWidget(today_header)
        self.today_list = QListWidget()
        self.today_list.setAlternatingRowColors(True)
        self.today_list.currentItemChanged.connect(self._on_task_selection_changed)
        today_layout.addWidget(self.today_list)
        self.tasks_splitter.addWidget(today_container)

        # All tasks container
        all_container = QWidget()
        all_layout = QVBoxLayout(all_container)
        all_layout.setContentsMargins(0, 0, 0, 0)
        all_header = QLabel("All Recurring Tasks")
        all_header.setStyleSheet("font-weight: bold; font-size: 15px; margin-top: 4px;")
        all_layout.addWidget(all_header)
        self.all_list = QListWidget()
        self.all_list.setAlternatingRowColors(True)
        self.all_list.currentItemChanged.connect(self._on_task_selection_changed)
        all_layout.addWidget(self.all_list)
        self.tasks_splitter.addWidget(all_container)

        self.tasks_splitter.setSizes([300, 300])
        layout.addWidget(self.tasks_splitter, 1)

    def _on_task_selection_changed(self, current, previous):
        """Enable Edit button when a task is selected."""
        task_id = self._get_selected_task_id()
        self.edit_btn.setEnabled(task_id is not None)

    def _get_selected_task_id(self):
        """Get task_id from the currently selected item in either list."""
        for list_widget in (self.today_list, self.all_list):
            item = list_widget.currentItem()
            if item and item.data(Qt.ItemDataRole.UserRole):
                return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _edit_selected_task(self):
        """Edit the currently selected task."""
        task_id = self._get_selected_task_id()
        if task_id:
            self._edit_task(task_id)

    def save_layout(self):
        """Save splitter sizes to QSettings."""
        qs = QSettings("ShipLog", "TaskLayout")
        qs.setValue("tasks_splitter", self.tasks_splitter.saveState())

    def _restore_layout(self):
        """Restore splitter sizes from QSettings."""
        qs = QSettings("ShipLog", "TaskLayout")
        state = qs.value("tasks_splitter")
        if state:
            self.tasks_splitter.restoreState(state)

    def refresh(self):
        """Reload tasks from database."""
        self._refresh_project_filter()
        self._load_today_tasks()
        self._load_all_tasks()

    def _refresh_project_filter(self):
        """Update the project filter combo with current projects."""
        self.project_filter.blockSignals(True)
        current_data = self.project_filter.currentData()
        self.project_filter.clear()
        self.project_filter.addItem("All Projects", None)
        self.project_filter.addItem("No Project", 0)
        projects = self.db.get_all_projects(status="Active")
        for p in projects:
            self.project_filter.addItem(p["title"], p["id"])
        # Restore previous selection
        if current_data is not None:
            idx = self.project_filter.findData(current_data)
            if idx >= 0:
                self.project_filter.setCurrentIndex(idx)
        self.project_filter.blockSignals(False)

    def _on_project_filter_changed(self, index: int):
        """Handle project filter selection change."""
        self._project_filter_id = self.project_filter.currentData()
        self._load_today_tasks()
        self._load_all_tasks()

    def _filter_tasks(self, tasks: list[dict]) -> list[dict]:
        """Apply project filter to task list."""
        if self._project_filter_id is None:
            return tasks
        if self._project_filter_id == 0:
            return [t for t in tasks if not t.get("project_id")]
        return [t for t in tasks if t.get("project_id") == self._project_filter_id]

    def _load_today_tasks(self):
        self.today_list.clear()
        today = date.today().isoformat()
        tasks = self._filter_tasks(self.db.get_tasks_due(today))
        if not tasks:
            item = QListWidgetItem("No tasks due today.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.today_list.addItem(item)
            return
        for task in tasks:
            self._add_task_item(self.today_list, task)

    def _load_all_tasks(self):
        self.all_list.clear()
        tasks = self._filter_tasks(self.db.get_all_tasks())
        if not tasks:
            item = QListWidgetItem("No recurring tasks configured.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.all_list.addItem(item)
            return
        for task in tasks:
            self._add_task_item(self.all_list, task)

    def _get_project_name(self, project_id) -> str:
        """Look up project title by ID."""
        if not project_id:
            return ""
        project = self.db.get_project(project_id)
        return project["title"] if project else ""

    def _add_task_item(self, list_widget: QListWidget, task: dict):
        project_name = self._get_project_name(task.get("project_id"))
        widget = TaskItemWidget(task, project_name=project_name)
        widget.completed.connect(self._on_task_completed)
        widget.navigate_to_project.connect(self.navigate_to_project.emit)
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 42))
        item.setData(Qt.ItemDataRole.UserRole, task["id"])
        list_widget.addItem(item)
        list_widget.setItemWidget(item, widget)

    def _add_task(self):
        projects = self.db.get_all_projects(status="Active")
        dialog = TaskDialog(projects=projects, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            if data["name"]:
                project_id = data.pop("project_id", None)
                task_id = self.db.create_task(
                    data["name"], data["priority"],
                    data["recurrence"], data.get("deadline"),
                )
                if project_id:
                    self.db.update_task(task_id, project_id=project_id)
                self.refresh()

    def _edit_task(self, task_id: int):
        task = self.db.get_task(task_id)
        if not task:
            return
        projects = self.db.get_all_projects(status="Active")
        dialog = TaskDialog(task=task, projects=projects, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            if data["name"]:
                self.db.update_task(task_id, **data)
                self.refresh()

    def _on_task_completed(self, task_id: int):
        self.db.complete_task(task_id)
        self.refresh()
