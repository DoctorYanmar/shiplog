"""QTimer-based recurring task checker and notification scheduler."""

import logging
from datetime import date, timedelta

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

logger = logging.getLogger(__name__)


class TaskScheduler(QObject):
    """Checks for due tasks periodically and emits notification signals."""

    tasks_due = pyqtSignal(list)       # list of task dicts due today
    tasks_upcoming = pyqtSignal(list)  # list of task dicts due tomorrow

    def __init__(self, database, check_interval_ms: int = 60_000, parent=None):
        super().__init__(parent)
        self.db = database
        self.timer = QTimer(self)
        self.timer.setInterval(check_interval_ms)
        self.timer.timeout.connect(self.check_tasks)

    def start(self):
        """Start periodic checking."""
        self.check_tasks()
        self.timer.start()
        logger.info("Task scheduler started (interval: %dms)", self.timer.interval())

    def stop(self):
        self.timer.stop()
        logger.info("Task scheduler stopped")

    def check_tasks(self):
        """Check for tasks due today and tomorrow."""
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()

        due_today = self.db.get_tasks_due(today)
        due_tomorrow = self.db.get_tasks_due(tomorrow)
        # tomorrow list includes today's too, so filter to only tomorrow
        today_ids = {t["id"] for t in due_today}
        upcoming = [t for t in due_tomorrow if t["id"] not in today_ids]

        if due_today:
            self.tasks_due.emit(due_today)
        if upcoming:
            self.tasks_upcoming.emit(upcoming)
