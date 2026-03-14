"""Project detail window with files, emails, notes, AI summary, and 'Needed' panel.

Uses QSplitter for resizable panels. Supports drag-and-drop for files/emails.
QTreeWidget with movable column headers for file/email lists.
Syncs folder contents with database on every refresh.
Layout (splitter sizes, column widths) persisted via QSettings.
"""

import os
import re
import html as html_mod
import logging
from pathlib import Path
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QDate, QSettings, QTimer
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSplitter, QFrame, QTextEdit, QLineEdit, QComboBox,
    QTreeWidget, QTreeWidgetItem, QHeaderView,
    QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox,
    QFormLayout, QDateEdit, QDialog, QDialogButtonBox,
    QScrollArea, QGroupBox, QSizePolicy, QInputDialog, QCheckBox,
)

logger = logging.getLogger(__name__)

NOTE_COLLAPSED_HEIGHT = 70


class ProjectCreateDialog(QDialog):
    """Dialog for creating or editing a project."""

    def __init__(self, project: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Project" if project else "New Project")
        self.setMinimumWidth(450)

        layout = QFormLayout(self)

        self.title_edit = QLineEdit()
        if project:
            self.title_edit.setText(project.get("title", ""))
        layout.addRow("Title:", self.title_edit)

        self.desc_edit = QTextEdit()
        self.desc_edit.setMaximumHeight(100)
        if project:
            self.desc_edit.setPlainText(project.get("description", ""))
        layout.addRow("Description:", self.desc_edit)

        self.priority_combo = QComboBox()
        self.priority_combo.addItems(["Low", "Medium", "High", "Critical"])
        if project:
            idx = self.priority_combo.findText(project.get("priority", "Medium"))
            if idx >= 0:
                self.priority_combo.setCurrentIndex(idx)
        else:
            self.priority_combo.setCurrentIndex(1)
        layout.addRow("Priority:", self.priority_combo)

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
        if project and project.get("deadline"):
            try:
                d = QDate.fromString(project["deadline"], "yyyy-MM-dd")
                if d.isValid():
                    self.has_deadline_cb.setChecked(True)
                    self.deadline_edit.setDate(d)
            except Exception:
                pass
        dl_row = QHBoxLayout()
        dl_row.addWidget(self.has_deadline_cb)
        dl_row.addWidget(self.deadline_edit, 1)
        layout.addRow("Deadline:", dl_row)

        self.needed_edit = QTextEdit()
        self.needed_edit.setMaximumHeight(80)
        self.needed_edit.setPlaceholderText("What's needed to close this project?")
        if project:
            self.needed_edit.setPlainText(project.get("whats_needed", ""))
        layout.addRow("What's Needed:", self.needed_edit)

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
            "title": self.title_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "priority": self.priority_combo.currentText(),
            "deadline": deadline,
            "whats_needed": self.needed_edit.toPlainText().strip(),
        }


class AISummaryDialog(QDialog):
    """Popup dialog showing full AI summary text, copyable."""

    def __init__(self, title: str, summary_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(500, 300)

        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(summary_text)
        self.text_edit.setReadOnly(True)
        layout.addWidget(self.text_edit)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _copy(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.text_edit.toPlainText())


class ProjectLinkDialog(QDialog):
    """Dialog for picking a project to link to from a note."""

    def __init__(self, projects: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Link to Project")
        self.setMinimumWidth(450)
        self.selected_project = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select a project to link:"))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Title", "Priority", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for p in projects:
            item = QTreeWidgetItem([p["title"], p["priority"], p["status"]])
            item.setData(0, Qt.ItemDataRole.UserRole, p["id"])
            self.tree.addTopLevelItem(item)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.tree)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_double_click(self, item, column):
        self.selected_project = {
            "id": item.data(0, Qt.ItemDataRole.UserRole),
            "title": item.text(0),
        }
        self.accept()

    def _on_accept(self):
        current = self.tree.currentItem()
        if current:
            self.selected_project = {
                "id": current.data(0, Qt.ItemDataRole.UserRole),
                "title": current.text(0),
            }
        self.accept()


class NoteEditDialog(QDialog):
    """Dialog for editing a note with project linking support."""

    def __init__(self, content: str, projects: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Note")
        self.setMinimumSize(500, 300)
        self.projects = projects

        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(content)
        layout.addWidget(self.text_edit)

        tool_row = QHBoxLayout()
        link_btn = QPushButton("Insert Project Link")
        link_btn.clicked.connect(self._insert_link)
        tool_row.addWidget(link_btn)
        tool_row.addStretch()
        layout.addLayout(tool_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _insert_link(self):
        dialog = ProjectLinkDialog(self.projects, parent=self)
        if dialog.exec() and dialog.selected_project:
            pid = dialog.selected_project["id"]
            title = dialog.selected_project["title"]
            cursor = self.text_edit.textCursor()
            selected = cursor.selectedText()
            display = selected if selected else title
            cursor.insertText(f"[[project:{pid}:{display}]]")

    def get_content(self) -> str:
        return self.text_edit.toPlainText().strip()


class DropZoneTree(QTreeWidget):
    """QTreeWidget that accepts file drops with resizable column headers."""

    files_dropped = pyqtSignal(list)

    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(columns)
        self.setAcceptDrops(True)
        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(False)
        self.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        header = self.header()
        header.setSectionsMovable(True)
        header.setStretchLastSection(False)
        for i in range(len(columns) - 1):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        if len(columns) > 0:
            header.setSectionResizeMode(len(columns) - 1, QHeaderView.ResizeMode.Stretch)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()
                     if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class ProjectWindow(QWidget):
    """Full project detail view with resizable panels."""

    project_updated = pyqtSignal()
    back_requested = pyqtSignal()
    navigate_to_project = pyqtSignal(int)

    def __init__(self, project_id: int, database, file_manager, settings: dict,
                 parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self.db = database
        self.fm = file_manager
        self.settings = settings
        self.project = self.db.get_project(project_id)
        self._full_ai_summary = ""
        self._build_ui()
        self._restore_layout()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # Top bar: [Back] [Priority] [Title] [Edit] [Description] [AI Summary] [Archive] [Open Folder]
        top_bar = QHBoxLayout()

        back_btn = QPushButton("< Back")
        back_btn.setProperty("flat", True)
        back_btn.clicked.connect(self._on_back)
        top_bar.addWidget(back_btn)

        self.priority_label = QLabel(self.project["priority"])
        self.priority_label.setProperty("priority", self.project["priority"])
        self.priority_label.setFixedHeight(24)
        self.priority_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_bar.addWidget(self.priority_label)

        self.title_label = QLabel(self.project["title"])
        self.title_label.setProperty("heading", True)
        top_bar.addWidget(self.title_label, 1)

        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._edit_project)
        top_bar.addWidget(edit_btn)

        desc_btn = QPushButton("Description")
        desc_btn.clicked.connect(self._show_description)
        top_bar.addWidget(desc_btn)

        ai_btn = QPushButton("AI Summary")
        ai_btn.clicked.connect(self._show_full_summary)
        top_bar.addWidget(ai_btn)

        archive_btn = QPushButton("Archive")
        archive_btn.setStyleSheet("background-color: #fab387;")
        archive_btn.clicked.connect(self._archive_project)
        top_bar.addWidget(archive_btn)

        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.clicked.connect(self._open_project_folder)
        top_bar.addWidget(open_folder_btn)

        main_layout.addLayout(top_bar)

        # Main content splitter (horizontal: left | right)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left panel: What's needed + Files + Emails (vertical splitter) ──
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)

        # "What's needed" — resizable top panel on left side
        needed_group = QGroupBox("What's needed to close")
        needed_layout = QVBoxLayout(needed_group)
        self.needed_text = QTextEdit()
        self.needed_text.setPlainText(self.project.get("whats_needed", ""))
        self.needed_text.textChanged.connect(self._save_whats_needed)
        needed_layout.addWidget(self.needed_text)
        self.left_splitter.addWidget(needed_group)

        # Files section
        files_group = QGroupBox("Files  (drag & drop here)")
        files_vlayout = QVBoxLayout(files_group)

        add_file_btn = QPushButton("+ Add File")
        add_file_btn.clicked.connect(self._add_file)
        files_vlayout.addWidget(add_file_btn)

        self.files_tree = DropZoneTree(["Description", "Filename", "Date"])
        self.files_tree.itemDoubleClicked.connect(self._open_file_from_tree)
        self.files_tree.files_dropped.connect(self._on_files_dropped)
        self.files_tree.header().resizeSection(0, 180)
        self.files_tree.header().resizeSection(1, 180)
        files_vlayout.addWidget(self.files_tree)

        files_btn_row = QHBoxLayout()
        files_btn_row.addStretch()
        self.file_edit_btn = QPushButton("Edit")
        self.file_edit_btn.setFixedWidth(70)
        self.file_edit_btn.clicked.connect(self._edit_selected_file)
        files_btn_row.addWidget(self.file_edit_btn)
        self.file_del_btn = QPushButton("Delete")
        self.file_del_btn.setFixedWidth(70)
        self.file_del_btn.setStyleSheet("background-color: #f38ba8;")
        self.file_del_btn.clicked.connect(self._delete_selected_file)
        files_btn_row.addWidget(self.file_del_btn)
        files_vlayout.addLayout(files_btn_row)

        self.left_splitter.addWidget(files_group)

        # Emails section
        emails_group = QGroupBox("Emails  (drag & drop .msg/.eml here)")
        emails_vlayout = QVBoxLayout(emails_group)

        add_email_btn = QPushButton("+ Add Email")
        add_email_btn.clicked.connect(self._add_email)
        emails_vlayout.addWidget(add_email_btn)

        self.emails_tree = DropZoneTree(["Description", "Filename", "Date"])
        self.emails_tree.itemDoubleClicked.connect(self._open_email_from_tree)
        self.emails_tree.files_dropped.connect(self._on_emails_dropped)
        self.emails_tree.header().resizeSection(0, 180)
        self.emails_tree.header().resizeSection(1, 180)
        emails_vlayout.addWidget(self.emails_tree)

        emails_btn_row = QHBoxLayout()
        emails_btn_row.addStretch()
        self.email_edit_btn = QPushButton("Edit")
        self.email_edit_btn.setFixedWidth(70)
        self.email_edit_btn.clicked.connect(self._edit_selected_email)
        emails_btn_row.addWidget(self.email_edit_btn)
        self.email_del_btn = QPushButton("Delete")
        self.email_del_btn.setFixedWidth(70)
        self.email_del_btn.setStyleSheet("background-color: #f38ba8;")
        self.email_del_btn.clicked.connect(self._delete_selected_email)
        emails_btn_row.addWidget(self.email_del_btn)
        emails_vlayout.addLayout(emails_btn_row)

        self.left_splitter.addWidget(emails_group)
        self.left_splitter.setSizes([120, 300, 300])

        self.main_splitter.addWidget(self.left_splitter)

        # ── Right panel: Notes + Linked Tasks (vertical splitter) ──
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)

        # Notes section
        notes_group = QGroupBox("Notes")
        notes_layout = QVBoxLayout(notes_group)

        self.note_input = QTextEdit()
        self.note_input.setMaximumHeight(80)
        self.note_input.setPlaceholderText("Write a new note...")
        notes_layout.addWidget(self.note_input)

        add_note_btn = QPushButton("Add Note")
        add_note_btn.clicked.connect(self._add_note)
        notes_layout.addWidget(add_note_btn)

        self.notes_area = QScrollArea()
        self.notes_area.setWidgetResizable(True)
        self.notes_content = QWidget()
        self.notes_list_layout = QVBoxLayout(self.notes_content)
        self.notes_list_layout.setSpacing(8)
        self.notes_list_layout.addStretch()
        self.notes_area.setWidget(self.notes_content)
        notes_layout.addWidget(self.notes_area)

        self.right_splitter.addWidget(notes_group)

        # Linked Tasks section
        tasks_group = QGroupBox("Linked Daily Tasks")
        tasks_vlayout = QVBoxLayout(tasks_group)

        tasks_btn_row = QHBoxLayout()
        link_task_btn = QPushButton("+ Link Task")
        link_task_btn.clicked.connect(self._link_existing_task)
        tasks_btn_row.addWidget(link_task_btn)
        add_task_btn = QPushButton("+ Add Task")
        add_task_btn.clicked.connect(self._add_linked_task)
        tasks_btn_row.addWidget(add_task_btn)
        tasks_btn_row.addStretch()
        tasks_vlayout.addLayout(tasks_btn_row)

        self.linked_tasks_area = QScrollArea()
        self.linked_tasks_area.setWidgetResizable(True)
        self.linked_tasks_content = QWidget()
        self.linked_tasks_layout = QVBoxLayout(self.linked_tasks_content)
        self.linked_tasks_layout.setSpacing(4)
        self.linked_tasks_layout.addStretch()
        self.linked_tasks_area.setWidget(self.linked_tasks_content)
        tasks_vlayout.addWidget(self.linked_tasks_area)

        self.right_splitter.addWidget(tasks_group)
        self.right_splitter.setSizes([400, 200])

        self.main_splitter.addWidget(self.right_splitter)
        self.main_splitter.setSizes([500, 400])
        main_layout.addWidget(self.main_splitter, 1)

    # ── Layout persistence ────────────────────────────────────

    def save_layout(self):
        """Save splitter sizes and column widths to QSettings."""
        qs = QSettings("ShipLog", "ProjectLayout")
        qs.setValue("main_splitter", self.main_splitter.saveState())
        qs.setValue("left_splitter", self.left_splitter.saveState())
        qs.setValue("right_splitter", self.right_splitter.saveState())
        qs.setValue("files_col_0", self.files_tree.columnWidth(0))
        qs.setValue("files_col_1", self.files_tree.columnWidth(1))
        qs.setValue("emails_col_0", self.emails_tree.columnWidth(0))
        qs.setValue("emails_col_1", self.emails_tree.columnWidth(1))

    def _restore_layout(self):
        """Restore splitter sizes and column widths from QSettings."""
        qs = QSettings("ShipLog", "ProjectLayout")
        state = qs.value("main_splitter")
        if state:
            self.main_splitter.restoreState(state)
        state = qs.value("left_splitter")
        if state:
            self.left_splitter.restoreState(state)
        state = qs.value("right_splitter")
        if state:
            self.right_splitter.restoreState(state)
        w = qs.value("files_col_0", type=int)
        if w and w > 0:
            self.files_tree.header().resizeSection(0, w)
        w = qs.value("files_col_1", type=int)
        if w and w > 0:
            self.files_tree.header().resizeSection(1, w)
        w = qs.value("emails_col_0", type=int)
        if w and w > 0:
            self.emails_tree.header().resizeSection(0, w)
        w = qs.value("emails_col_1", type=int)
        if w and w > 0:
            self.emails_tree.header().resizeSection(1, w)

    def _on_back(self):
        """Save layout and emit back signal."""
        self.save_layout()
        self.back_requested.emit()

    # ── Refresh & Sync ────────────────────────────────────────

    def refresh(self):
        """Reload all project data from database, sync folder first."""
        self.project = self.db.get_project(self.project_id)
        if not self.project:
            return

        self._sync_folder_with_db()

        self.title_label.setText(self.project["title"])
        self.priority_label.setText(self.project["priority"])
        self.priority_label.setProperty("priority", self.project["priority"])
        self.priority_label.style().unpolish(self.priority_label)
        self.priority_label.style().polish(self.priority_label)
        self.needed_text.blockSignals(True)
        self.needed_text.setPlainText(self.project.get("whats_needed", ""))
        self.needed_text.blockSignals(False)

        stored_summary = self.project.get("ai_summary", "")
        if stored_summary:
            if "---FULL---" in stored_summary:
                self._full_ai_summary = stored_summary.split("---FULL---", 1)[1].strip()
            else:
                self._full_ai_summary = stored_summary
        else:
            self._full_ai_summary = ""

        self._load_files()
        self._load_emails()
        self._load_notes()
        self._load_linked_tasks()

    def _sync_folder_with_db(self):
        """Sync disk folder contents with database records."""
        folder = self.fm.get_project_folder(self.project_id)
        if not folder or not folder.exists():
            return

        # Sync files
        files_dir = folder / "files"
        if files_dir.exists():
            db_files = self.db.get_files(self.project_id)
            db_paths = {f["stored_path"] for f in db_files}
            db_filenames = {f["filename"] for f in db_files}

            for f in db_files:
                if not Path(f["stored_path"]).exists():
                    logger.info("[SYNC] Removing missing file from DB: %s", f["stored_path"])
                    self.db.delete_file(f["id"])

            for disk_file in files_dir.iterdir():
                if disk_file.is_file() and str(disk_file) not in db_paths:
                    if disk_file.name not in db_filenames:
                        ftype = self.fm.get_file_type(disk_file.name)
                        logger.info("[SYNC] Adding new file from disk: %s", disk_file)
                        self.db.add_file(
                            self.project_id, disk_file.name, str(disk_file), ftype
                        )

        # Sync emails
        emails_dir = folder / "emails"
        if emails_dir.exists():
            db_emails = self.db.get_emails(self.project_id)
            db_paths = {e["stored_path"] for e in db_emails}
            db_filenames = {e["filename"] for e in db_emails}

            for e in db_emails:
                if not Path(e["stored_path"]).exists():
                    logger.info("[SYNC] Removing missing email from DB: %s", e["stored_path"])
                    self.db.delete_email(e["id"])

            for disk_file in emails_dir.iterdir():
                if disk_file.is_file() and str(disk_file) not in db_paths:
                    if disk_file.name not in db_filenames:
                        logger.info("[SYNC] Adding new email from disk: %s", disk_file)
                        self.db.add_email(
                            self.project_id, disk_file.name, str(disk_file)
                        )

    # ── Show AI Summary ───────────────────────────────────────

    def _show_full_summary(self):
        text = self._full_ai_summary or "No AI summary available."
        dialog = AISummaryDialog("AI Summary", text, parent=self)
        dialog.exec()

    # ── Load Lists ────────────────────────────────────────────

    def _load_files(self):
        self.files_tree.clear()
        files = self.db.get_files(self.project_id)
        for f in files:
            date_str = f.get("added_at", "")[:10] if f.get("added_at") else ""
            item = QTreeWidgetItem([
                f.get("note", ""),
                f.get("filename", ""),
                date_str,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, f)
            self.files_tree.addTopLevelItem(item)

    def _load_emails(self):
        self.emails_tree.clear()
        emails = self.db.get_emails(self.project_id)
        for e in emails:
            date_str = e.get("added_at", "")[:10] if e.get("added_at") else ""
            desc = e.get("note", "") or e.get("subject", "")
            item = QTreeWidgetItem([
                desc,
                e.get("filename", ""),
                date_str,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, e)
            self.emails_tree.addTopLevelItem(item)

    def _load_notes(self):
        while self.notes_list_layout.count() > 1:
            item = self.notes_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        notes = self.db.get_notes(self.project_id)
        for note in notes:
            note_frame = QFrame()
            note_frame.setProperty("card", True)
            frame_layout = QVBoxLayout(note_frame)

            ts = QLabel(note["created_at"][:16] if note.get("created_at") else "")
            ts.setStyleSheet("font-size: 11px; color: #6c7086;")
            frame_layout.addWidget(ts)

            # Content in a container for collapsing
            content_container = QWidget()
            content_vlayout = QVBoxLayout(content_container)
            content_vlayout.setContentsMargins(0, 0, 0, 0)

            content_label = QLabel(self._render_note_html(note["content"]))
            content_label.setTextFormat(Qt.TextFormat.RichText)
            content_label.setWordWrap(True)
            content_label.setOpenExternalLinks(False)
            content_label.linkActivated.connect(self._on_note_link_clicked)
            content_vlayout.addWidget(content_label)

            frame_layout.addWidget(content_container)

            # Check if note is long enough to need collapsing
            is_long = len(note["content"]) > 150 or note["content"].count('\n') > 2

            btn_row = QHBoxLayout()

            if is_long:
                content_container.setMaximumHeight(NOTE_COLLAPSED_HEIGHT)
                toggle_btn = QPushButton("▼ More")
                toggle_btn.setFixedHeight(20)
                toggle_btn.setStyleSheet(
                    "font-size: 10px; padding: 1px 8px; border: 1px solid #6c7086; "
                    "border-radius: 3px; color: #89b4fa; background: transparent;"
                )
                toggle_btn.clicked.connect(
                    lambda checked, c=content_container, b=toggle_btn:
                        self._toggle_note_collapse(c, b)
                )
                btn_row.addWidget(toggle_btn)

            btn_row.addStretch()

            note_id = note["id"]
            note_content = note["content"]

            edit_btn = QPushButton("Edit")
            edit_btn.setFixedWidth(60)
            edit_btn.setStyleSheet("font-size: 11px; padding: 3px;")
            edit_btn.clicked.connect(
                lambda checked, nid=note_id, nc=note_content: self._edit_note(nid, nc)
            )
            btn_row.addWidget(edit_btn)

            link_btn = QPushButton("Link")
            link_btn.setFixedWidth(60)
            link_btn.setStyleSheet("font-size: 11px; padding: 3px;")
            link_btn.clicked.connect(
                lambda checked, nid=note_id, nc=note_content: self._link_note(nid, nc)
            )
            btn_row.addWidget(link_btn)

            del_btn = QPushButton("Delete")
            del_btn.setFixedWidth(60)
            del_btn.setStyleSheet("background-color: #f38ba8; font-size: 11px; padding: 3px;")
            del_btn.clicked.connect(
                lambda checked, nid=note_id: self._delete_note(nid)
            )
            btn_row.addWidget(del_btn)

            frame_layout.addLayout(btn_row)
            self.notes_list_layout.insertWidget(self.notes_list_layout.count() - 1, note_frame)

    def _load_linked_tasks(self):
        """Load daily tasks linked to this project."""
        while self.linked_tasks_layout.count() > 1:
            item = self.linked_tasks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        tasks = self.db.get_tasks_by_project(self.project_id)
        if not tasks:
            empty = QLabel("No linked tasks.")
            empty.setStyleSheet("font-size: 12px; color: #6c7086; padding: 8px;")
            self.linked_tasks_layout.insertWidget(0, empty)
            return

        for task in tasks:
            row = QFrame()
            row.setProperty("card", True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)

            # Done checkbox
            task_id = task["id"]
            done_cb = QCheckBox()
            done_cb.setFixedSize(24, 24)
            done_cb.toggled.connect(
                lambda checked, tid=task_id: self._complete_linked_task(tid) if checked else None
            )
            row_layout.addWidget(done_cb)

            name = QLabel(task["name"])
            name.setStyleSheet("font-weight: bold;")
            row_layout.addWidget(name, 1)

            priority_label = QLabel(task["priority"])
            priority_label.setProperty("priority", task["priority"])
            priority_label.setFixedWidth(60)
            priority_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row_layout.addWidget(priority_label)

            rec_text = task["recurrence"].capitalize()
            if task["recurrence"] == "once":
                rec_text = "One-time"
            rec_label = QLabel(rec_text)
            rec_label.setFixedWidth(70)
            rec_label.setStyleSheet("font-size: 11px; color: #6c7086;")
            row_layout.addWidget(rec_label)

            due_label = QLabel(f"Due: {task['next_due'] or 'N/A'}")
            due_label.setFixedWidth(100)
            due_label.setStyleSheet("font-size: 11px;")
            row_layout.addWidget(due_label)

            unlink_btn = QPushButton("Unlink")
            unlink_btn.setFixedWidth(55)
            unlink_btn.setStyleSheet("background-color: #f38ba8; font-size: 11px; padding: 2px;")
            unlink_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            unlink_btn.clicked.connect(
                lambda checked, tid=task_id: self._unlink_task(tid)
            )
            row_layout.addWidget(unlink_btn)

            self.linked_tasks_layout.insertWidget(
                self.linked_tasks_layout.count() - 1, row
            )

    def _complete_linked_task(self, task_id: int):
        """Complete a linked task (archive it)."""
        self.db.complete_task(task_id)
        self._load_linked_tasks()
        self.project_updated.emit()

    def _link_existing_task(self):
        """Show dialog to link an existing task to this project."""
        all_tasks = self.db.get_all_tasks()
        available = [t for t in all_tasks if t.get("project_id") != self.project_id]
        if not available:
            QMessageBox.information(
                self, "No Tasks",
                "No unlinked tasks available. Use '+ Add Task' to create one."
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Link Task to Project")
        dialog.setMinimumWidth(400)
        dlayout = QVBoxLayout(dialog)
        dlayout.addWidget(QLabel("Select tasks to link:"))

        task_list = QListWidget()
        task_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for t in available:
            label = f"{t['name']}  ({t['priority']}, {t['recurrence']})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, t["id"])
            task_list.addItem(item)
        dlayout.addWidget(task_list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dlayout.addWidget(buttons)

        if dialog.exec():
            for item in task_list.selectedItems():
                task_id = item.data(Qt.ItemDataRole.UserRole)
                self.db.update_task(task_id, project_id=self.project_id)
            self._load_linked_tasks()
            self.project_updated.emit()

    def _add_linked_task(self):
        """Create a new task already linked to this project."""
        from shiplog.ui.task_widget import TaskDialog
        projects = self.db.get_all_projects(status="Active")
        dialog = TaskDialog(projects=projects, parent=self)
        # Pre-select current project
        idx = dialog.project_combo.findData(self.project_id)
        if idx >= 0:
            dialog.project_combo.setCurrentIndex(idx)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            if data["name"]:
                project_id = data.pop("project_id", None) or self.project_id
                task_id = self.db.create_task(
                    data["name"], data["priority"],
                    data["recurrence"], data.get("deadline"),
                )
                self.db.update_task(task_id, project_id=project_id)
                self._load_linked_tasks()
                self.project_updated.emit()

    def _show_description(self):
        """Show project description in a popup dialog."""
        desc = self.project.get("description", "") or "No description."
        dialog = AISummaryDialog(
            f"Description — {self.project['title']}", desc, parent=self
        )
        dialog.exec()

    def _unlink_task(self, task_id: int):
        """Remove the project link from a task."""
        self.db.update_task(task_id, project_id=None)
        self._load_linked_tasks()
        self.project_updated.emit()

    def _toggle_note_collapse(self, container: QWidget, button: QPushButton):
        """Toggle note between collapsed and expanded."""
        if container.maximumHeight() == NOTE_COLLAPSED_HEIGHT:
            container.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
            button.setText("▲ Less")
        else:
            container.setMaximumHeight(NOTE_COLLAPSED_HEIGHT)
            button.setText("▼ More")

    # ── Note rendering ────────────────────────────────────────

    def _render_note_html(self, text: str) -> str:
        """Convert note text with [[project:ID:Title]] links to clickable HTML."""
        parts = re.split(r'(\[\[project:\d+:[^\]]+\]\])', text)
        result_parts = []
        for part in parts:
            m = re.match(r'\[\[project:(\d+):([^\]]+)\]\]', part)
            if m:
                pid = m.group(1)
                title = html_mod.escape(m.group(2))
                result_parts.append(
                    f'<a href="project:{pid}" '
                    f'style="color: #89b4fa; text-decoration: underline;">'
                    f'{title}</a>'
                )
            else:
                result_parts.append(html_mod.escape(part))
        return ''.join(result_parts).replace('\n', '<br>')

    def _on_note_link_clicked(self, url: str):
        if url.startswith("project:"):
            try:
                pid = int(url.split(":")[1])
                self.navigate_to_project.emit(pid)
            except (ValueError, IndexError):
                pass

    # ── File/Email Actions ────────────────────────────────────

    def _save_whats_needed(self):
        text = self.needed_text.toPlainText().strip()
        self.db.update_project(self.project_id, whats_needed=text)

    def _ask_description(self, filename: str) -> str:
        text, ok = QInputDialog.getText(
            self, "Description",
            f"Short description for '{filename}':\n(leave empty to skip)",
        )
        return text.strip() if ok else ""

    def _add_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Files to Add")
        self._import_files(paths)

    def _on_files_dropped(self, paths: list):
        self._import_files(paths)

    def _import_files(self, paths: list):
        for path in paths:
            fname = Path(path).name
            note = self._ask_description(fname)
            filename, stored = self.fm.copy_file_to_project(
                path, self.project_id, self.project["title"]
            )
            file_type = self.fm.get_file_type(filename)
            self.db.add_file(self.project_id, filename, stored, file_type, note=note)
        if paths:
            self._load_files()
            self.project_updated.emit()

    def _add_email(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Email Files",
            "", "Email Files (*.msg *.eml);;All Files (*)"
        )
        self._import_emails(paths)

    def _on_emails_dropped(self, paths: list):
        email_paths = [p for p in paths if p.lower().endswith((".msg", ".eml"))]
        non_email = [p for p in paths if not p.lower().endswith((".msg", ".eml"))]
        if email_paths:
            self._import_emails(email_paths)
        if non_email:
            self._import_files(non_email)

    def _import_emails(self, paths: list):
        for path in paths:
            fname = Path(path).name
            note = self._ask_description(fname)
            filename, stored = self.fm.copy_email_to_project(
                path, self.project_id, self.project["title"]
            )
            from shiplog.core.email_parser import parse_email
            parsed = parse_email(path)
            if parsed:
                self.db.add_email(
                    self.project_id, filename, stored,
                    sender=parsed["sender"],
                    subject=parsed["subject"],
                    email_date=parsed["date"],
                    body_preview=parsed["body_preview"],
                    note=note,
                    body_full=parsed.get("body_full", ""),
                )
            else:
                self.db.add_email(self.project_id, filename, stored, note=note)
        if paths:
            self._load_emails()
            self.project_updated.emit()

    def _open_file_from_tree(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.fm.open_file(data["stored_path"])

    def _open_email_from_tree(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.fm.open_file(data["stored_path"])

    def _edit_selected_file(self):
        item = self.files_tree.currentItem()
        if not item:
            QMessageBox.information(self, "No Selection", "Select a file first.")
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        text, ok = QInputDialog.getText(
            self, "Edit Description",
            f"Description for '{data['filename']}':",
            text=data.get("note", ""),
        )
        if ok:
            self.db.update_file_note(data["id"], text.strip())
            self._load_files()

    def _delete_selected_file(self):
        item = self.files_tree.currentItem()
        if not item:
            QMessageBox.information(self, "No Selection", "Select a file first.")
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        reply = QMessageBox.question(
            self, "Delete File",
            "Delete this file from the project?\n(File will also be removed from disk)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.fm.delete_stored_file(data["stored_path"])
            self.db.delete_file(data["id"])
            self._load_files()
            self.project_updated.emit()

    def _edit_selected_email(self):
        item = self.emails_tree.currentItem()
        if not item:
            QMessageBox.information(self, "No Selection", "Select an email first.")
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        text, ok = QInputDialog.getText(
            self, "Edit Description",
            f"Description for '{data['filename']}':",
            text=data.get("note", ""),
        )
        if ok:
            self.db.update_email_note(data["id"], text.strip())
            self._load_emails()

    def _delete_selected_email(self):
        item = self.emails_tree.currentItem()
        if not item:
            QMessageBox.information(self, "No Selection", "Select an email first.")
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        reply = QMessageBox.question(
            self, "Delete Email",
            "Delete this email from the project?\n(File will also be removed from disk)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.fm.delete_stored_file(data["stored_path"])
            self.db.delete_email(data["id"])
            self._load_emails()
            self.project_updated.emit()

    # ── Notes Actions ─────────────────────────────────────────

    def _add_note(self):
        text = self.note_input.toPlainText().strip()
        if not text:
            return
        self.db.add_note(self.project_id, text)
        self.note_input.clear()
        self._load_notes()
        self.project_updated.emit()

    def _edit_note(self, note_id: int, current_content: str):
        all_projects = self.db.get_all_projects("Active") + self.db.get_all_projects("Archived")
        projects = [p for p in all_projects if p["id"] != self.project_id]

        dialog = NoteEditDialog(current_content, projects, parent=self)
        if dialog.exec():
            new_content = dialog.get_content()
            if new_content:
                self.db.update_note(note_id, new_content)
                self._load_notes()
                self.project_updated.emit()

    def _link_note(self, note_id: int, current_content: str):
        """Add a project link to an existing note."""
        all_projects = self.db.get_all_projects("Active") + self.db.get_all_projects("Archived")
        projects = [p for p in all_projects if p["id"] != self.project_id]

        dialog = ProjectLinkDialog(projects, parent=self)
        if dialog.exec() and dialog.selected_project:
            pid = dialog.selected_project["id"]
            title = dialog.selected_project["title"]
            new_content = current_content + f"\n[[project:{pid}:{title}]]"
            self.db.update_note(note_id, new_content)
            self._load_notes()

    def _delete_note(self, note_id: int):
        reply = QMessageBox.question(
            self, "Delete Note", "Delete this note?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_note(note_id)
            self._load_notes()

    # ── Project Actions ───────────────────────────────────────

    def _edit_project(self):
        dialog = ProjectCreateDialog(project=self.project, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            if data["title"]:
                self.db.update_project(self.project_id, **data)
                self.refresh()
                self.project_updated.emit()

    def _archive_project(self):
        reply = QMessageBox.question(
            self, "Archive Project",
            f"Archive project '{self.project['title']}'?\n"
            "It will be moved to the Archive tab.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.save_layout()
            self.db.update_project(self.project_id, status="Archived")
            self.project_updated.emit()
            self.back_requested.emit()

    def _open_project_folder(self):
        folder = self.fm.get_project_folder(self.project_id)
        if folder:
            self.fm.open_folder(str(folder))

    def update_ai_summary(self, short_summary: str, full_summary: str):
        """Called when AI summary is ready. Stores both parts."""
        combined = f"{short_summary}\n---FULL---\n{full_summary}"
        self.db.update_project(self.project_id, ai_summary=combined)
        self._full_ai_summary = full_summary

    # ── Search highlight support ─────────────────────────────

    def highlight_item(self, item_type: str, item_id: int,
                       search_query: str = ""):
        """Scroll to and highlight a specific item in the project view.

        Called when user clicks a search result to navigate here.
        """
        if item_type == "email":
            self._highlight_tree_item(self.emails_tree, item_id)
        elif item_type == "file":
            self._highlight_tree_item(self.files_tree, item_id)
        elif item_type == "note":
            self._highlight_note_item(item_id)

    def _highlight_tree_item(self, tree, item_id: int):
        """Find and select a tree item by its source ID."""
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("id") == item_id:
                tree.setCurrentItem(item)
                tree.scrollToItem(item)
                # Flash highlight using temporary background
                for col in range(tree.columnCount()):
                    item.setBackground(col, QColor("#f9e2af"))
                # Remove highlight after 3 seconds
                QTimer.singleShot(
                    3000,
                    lambda it=item, cols=tree.columnCount(): [
                        it.setBackground(c, QColor()) for c in range(cols)
                    ]
                )
                return

    def _highlight_note_item(self, note_id: int):
        """Scroll to and highlight a specific note frame."""
        for i in range(self.notes_list_layout.count() - 1):
            item = self.notes_list_layout.itemAt(i)
            if not item or not item.widget():
                continue
            frame = item.widget()
            # Notes don't store ID directly, but we can match by checking
            # the content. For a proper match we check the note buttons
            # which capture note_id in their lambda closures.
            # Simpler approach: flash-highlight all note frames briefly,
            # then try to scroll to the approximate position.
            pass

        # Scroll to approximate position by iterating notes from DB
        notes = self.db.get_all_notes(self.project_id)
        target_idx = None
        for idx, note in enumerate(notes):
            if note["id"] == note_id:
                target_idx = idx
                break

        if target_idx is not None and target_idx < self.notes_list_layout.count() - 1:
            item = self.notes_list_layout.itemAt(target_idx)
            if item and item.widget():
                frame = item.widget()
                # Flash highlight
                frame.setStyleSheet(
                    frame.styleSheet() + " border: 2px solid #f9e2af;"
                )
                self.notes_area.ensureWidgetVisible(frame)
                QTimer.singleShot(
                    3000,
                    lambda f=frame: f.setStyleSheet(
                        f.styleSheet().replace(" border: 2px solid #f9e2af;", "")
                    )
                )
