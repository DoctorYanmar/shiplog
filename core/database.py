"""SQLite database layer for ShipLog.

All database operations go through this module — never raw SQL in UI code.
"""

import sqlite3
import os
import logging
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_VERSION = 1


def _get_default_db_path() -> Path:
    return Path.home() / "ShipLog" / "data" / "shiplog.db"


class Database:
    """SQLite CRUD operations for projects, files, notes, tasks, and AI summaries."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(_get_default_db_path())
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._write_lock = threading.Lock()
        self._create_tables()
        self._migrate_tables()

    def _migrate_tables(self):
        """Handle schema migrations for existing databases."""
        cols = [row[1] for row in self.conn.execute(
            "PRAGMA table_info(daily_tasks)"
        ).fetchall()]
        if cols and "deadline" not in cols:
            logger.info("[DB] Migrating daily_tasks: adding deadline column, 'once' recurrence")
            self.conn.executescript("""
                ALTER TABLE daily_tasks RENAME TO daily_tasks_old;

                CREATE TABLE daily_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    priority TEXT DEFAULT 'Medium' CHECK(priority IN ('Low','Medium','High','Critical')),
                    recurrence TEXT DEFAULT 'daily' CHECK(recurrence IN ('once','daily','weekly','monthly','monday','tuesday','wednesday','thursday','friday','saturday','sunday')),
                    next_due DATE,
                    deadline DATE,
                    last_completed DATE,
                    enabled INTEGER DEFAULT 1
                );

                INSERT INTO daily_tasks (id, name, priority, recurrence, next_due, last_completed, enabled)
                    SELECT id, name, priority, recurrence, next_due, last_completed, enabled
                    FROM daily_tasks_old;

                DROP TABLE daily_tasks_old;
            """)
            self.conn.commit()
            logger.info("[DB] daily_tasks migration complete")

        # Add ai_summary_at column to projects if missing
        proj_cols = [row[1] for row in self.conn.execute(
            "PRAGMA table_info(projects)"
        ).fetchall()]
        if proj_cols and "ai_summary_at" not in proj_cols:
            try:
                self.conn.execute("ALTER TABLE projects ADD COLUMN ai_summary_at TIMESTAMP")
                self.conn.commit()
                logger.info("[DB] Added ai_summary_at column to projects")
            except Exception:
                pass

        # Add project_id column to daily_tasks if missing (task-project linking)
        task_cols = [row[1] for row in self.conn.execute(
            "PRAGMA table_info(daily_tasks)"
        ).fetchall()]
        if task_cols and "project_id" not in task_cols:
            try:
                self.conn.execute(
                    "ALTER TABLE daily_tasks ADD COLUMN project_id INTEGER "
                    "REFERENCES projects(id) ON DELETE SET NULL"
                )
                self.conn.commit()
                logger.info("[DB] Added project_id column to daily_tasks")
            except Exception:
                pass

        # Add body_full column to project_emails if missing
        email_cols = [row[1] for row in self.conn.execute(
            "PRAGMA table_info(project_emails)"
        ).fetchall()]
        if email_cols and "body_full" not in email_cols:
            try:
                self.conn.execute(
                    "ALTER TABLE project_emails ADD COLUMN body_full TEXT DEFAULT ''"
                )
                self.conn.commit()
                logger.info("[DB] Added body_full column to project_emails")
            except Exception:
                pass

    def _create_tables(self):
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                priority TEXT DEFAULT 'Medium' CHECK(priority IN ('Low','Medium','High','Critical')),
                status TEXT DEFAULT 'Active' CHECK(status IN ('Active','Archived')),
                deadline DATE,
                whats_needed TEXT DEFAULT '',
                ai_summary TEXT DEFAULT '',
                ai_summary_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS project_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                file_type TEXT DEFAULT '',
                stored_path TEXT NOT NULL,
                note TEXT DEFAULT '',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS project_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                sender TEXT DEFAULT '',
                subject TEXT DEFAULT '',
                email_date TIMESTAMP,
                body_preview TEXT DEFAULT '',
                note TEXT DEFAULT '',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                linked_file_id INTEGER,
                linked_email_id INTEGER,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY (linked_file_id) REFERENCES project_files(id) ON DELETE SET NULL,
                FOREIGN KEY (linked_email_id) REFERENCES project_emails(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS daily_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                priority TEXT DEFAULT 'Medium' CHECK(priority IN ('Low','Medium','High','Critical')),
                recurrence TEXT DEFAULT 'daily' CHECK(recurrence IN ('once','daily','weekly','monthly','monday','tuesday','wednesday','thursday','friday','saturday','sunday')),
                next_due DATE,
                deadline DATE,
                last_completed DATE,
                enabled INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS ai_digests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS file_content (
                file_id INTEGER PRIMARY KEY,
                content TEXT DEFAULT '',
                extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES project_files(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS search_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                project_id INTEGER DEFAULT 0,
                embedding BLOB NOT NULL,
                UNIQUE(source_type, source_id)
            );
        """)
        self.conn.commit()

        # FTS5 virtual table (cannot be inside executescript with other DDL)
        try:
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                    source_type,
                    source_id UNINDEXED,
                    project_id UNINDEXED,
                    title,
                    body,
                    tokenize='porter unicode61'
                )
            """)
            self.conn.commit()
        except Exception:
            logger.exception("Failed to create FTS5 search_index table")

    def close(self):
        if self.conn:
            self.conn.close()

    # ── Projects ──────────────────────────────────────────────

    def create_project(self, title: str, description: str = "",
                       priority: str = "Medium", deadline: Optional[str] = None,
                       whats_needed: str = "") -> int:
        cur = self.conn.execute(
            """INSERT INTO projects (title, description, priority, deadline, whats_needed)
               VALUES (?, ?, ?, ?, ?)""",
            (title, description, priority, deadline, whats_needed)
        )
        self.conn.commit()
        pid = cur.lastrowid
        self._index_content("project", pid, pid, title,
                            f"{description} {whats_needed}")
        return pid

    def get_project(self, project_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_projects(self, status: str = "Active") -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM projects WHERE status = ? ORDER BY "
            "CASE priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 "
            "WHEN 'Medium' THEN 2 WHEN 'Low' THEN 3 END, "
            "CASE WHEN deadline IS NOT NULL AND deadline < date('now') THEN 0 ELSE 1 END, "
            "deadline ASC NULLS LAST, last_modified_at DESC",
            (status,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_project(self, project_id: int, **kwargs) -> None:
        allowed = {"title", "description", "priority", "status", "deadline",
                    "whats_needed", "ai_summary", "ai_summary_at", "closed_at"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        fields["last_modified_at"] = datetime.now().isoformat()
        if fields.get("status") == "Archived" and "closed_at" not in fields:
            fields["closed_at"] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [project_id]
        self.conn.execute(
            f"UPDATE projects SET {set_clause} WHERE id = ?", values
        )
        self.conn.commit()
        # Re-index if searchable fields changed
        searchable = {"title", "description", "whats_needed", "ai_summary"}
        if searchable & set(kwargs.keys()):
            project = self.get_project(project_id)
            if project:
                self._deindex_content("project", project_id)
                self._index_content(
                    "project", project_id, project_id,
                    project["title"],
                    f"{project['description']} {project['whats_needed']} {project.get('ai_summary', '')}"
                )

    def delete_project(self, project_id: int) -> None:
        # Deindex all content belonging to this project
        self._deindex_project(project_id)
        self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self.conn.commit()

    def search_projects(self, query: str, status: Optional[str] = None) -> list[dict]:
        sql = ("SELECT * FROM projects WHERE "
               "(title LIKE ? OR description LIKE ? OR whats_needed LIKE ?)")
        params: list = [f"%{query}%"] * 3
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY last_modified_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Project Files ─────────────────────────────────────────

    def add_file(self, project_id: int, filename: str, stored_path: str,
                 file_type: str = "", note: str = "") -> int:
        cur = self.conn.execute(
            """INSERT INTO project_files (project_id, filename, stored_path, file_type, note)
               VALUES (?, ?, ?, ?, ?)""",
            (project_id, filename, stored_path, file_type, note)
        )
        self._touch_project(project_id)
        self.conn.commit()
        file_id = cur.lastrowid
        self._index_content("file", file_id, project_id, filename, note)
        return file_id

    def get_files(self, project_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM project_files WHERE project_id = ? ORDER BY added_at DESC",
            (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_file_note(self, file_id: int, note: str) -> None:
        self.conn.execute(
            "UPDATE project_files SET note = ? WHERE id = ?", (note, file_id)
        )
        self.conn.commit()

    def delete_file(self, file_id: int) -> None:
        self._deindex_content("file", file_id)
        self._deindex_content("file_content", file_id)
        self.conn.execute("DELETE FROM project_files WHERE id = ?", (file_id,))
        self.conn.commit()

    # ── Project Emails ────────────────────────────────────────

    def add_email(self, project_id: int, filename: str, stored_path: str,
                  sender: str = "", subject: str = "", email_date: str = "",
                  body_preview: str = "", note: str = "",
                  body_full: str = "") -> int:
        cur = self.conn.execute(
            """INSERT INTO project_emails
               (project_id, filename, stored_path, sender, subject, email_date,
                body_preview, note, body_full)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, filename, stored_path, sender, subject, email_date,
             body_preview, note, body_full)
        )
        self._touch_project(project_id)
        self.conn.commit()
        email_id = cur.lastrowid
        self._index_content("email", email_id, project_id,
                            subject, f"{sender} {body_full or body_preview} {note}")
        return email_id

    def get_emails(self, project_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM project_emails WHERE project_id = ? ORDER BY added_at DESC",
            (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_email_note(self, email_id: int, note: str) -> None:
        self.conn.execute(
            "UPDATE project_emails SET note = ? WHERE id = ?", (note, email_id)
        )
        self.conn.commit()

    def delete_email(self, email_id: int) -> None:
        self._deindex_content("email", email_id)
        self.conn.execute("DELETE FROM project_emails WHERE id = ?", (email_id,))
        self.conn.commit()

    # ── Notes ─────────────────────────────────────────────────

    def add_note(self, project_id: int, content: str,
                 linked_file_id: Optional[int] = None,
                 linked_email_id: Optional[int] = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO notes (project_id, content, linked_file_id, linked_email_id)
               VALUES (?, ?, ?, ?)""",
            (project_id, content, linked_file_id, linked_email_id)
        )
        self._touch_project(project_id)
        self.conn.commit()
        note_id = cur.lastrowid
        snippet = content[:100] if len(content) > 100 else content
        self._index_content("note", note_id, project_id, snippet, content)
        return note_id

    def get_notes(self, project_id: int, linked_file_id: Optional[int] = None,
                  linked_email_id: Optional[int] = None) -> list[dict]:
        sql = "SELECT * FROM notes WHERE project_id = ?"
        params: list = [project_id]
        if linked_file_id is not None:
            sql += " AND linked_file_id = ?"
            params.append(linked_file_id)
        elif linked_email_id is not None:
            sql += " AND linked_email_id = ?"
            params.append(linked_email_id)
        else:
            sql += " AND linked_file_id IS NULL AND linked_email_id IS NULL"
        sql += " ORDER BY created_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_all_notes(self, project_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_note(self, note_id: int, content: str) -> None:
        self.conn.execute(
            "UPDATE notes SET content = ? WHERE id = ?", (content, note_id)
        )
        self.conn.commit()
        # Re-index the note
        row = self.conn.execute(
            "SELECT project_id FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if row:
            self._deindex_content("note", note_id)
            snippet = content[:100] if len(content) > 100 else content
            self._index_content("note", note_id, row["project_id"], snippet, content)

    def delete_note(self, note_id: int) -> None:
        self._deindex_content("note", note_id)
        self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.conn.commit()

    # ── Daily Tasks ───────────────────────────────────────────

    def create_task(self, name: str, priority: str = "Medium",
                    recurrence: str = "daily",
                    deadline: Optional[str] = None) -> int:
        if recurrence == "once" and deadline:
            next_due = deadline
        elif recurrence == "once":
            next_due = date.today().isoformat()
        else:
            next_due = self._calc_next_due(recurrence)
        cur = self.conn.execute(
            """INSERT INTO daily_tasks (name, priority, recurrence, next_due, deadline)
               VALUES (?, ?, ?, ?, ?)""",
            (name, priority, recurrence, next_due, deadline)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_all_tasks(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM daily_tasks WHERE enabled = 1 ORDER BY next_due ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_tasks_due(self, target_date: Optional[str] = None) -> list[dict]:
        if target_date is None:
            target_date = date.today().isoformat()
        rows = self.conn.execute(
            "SELECT * FROM daily_tasks WHERE enabled = 1 AND next_due <= ? "
            "ORDER BY CASE priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 "
            "WHEN 'Medium' THEN 2 WHEN 'Low' THEN 3 END",
            (target_date,)
        ).fetchall()
        return [dict(r) for r in rows]

    def complete_task(self, task_id: int) -> None:
        task = self.conn.execute(
            "SELECT * FROM daily_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not task:
            return
        if task["recurrence"] == "once":
            # One-time task: mark done and disable (archive)
            self.conn.execute(
                "UPDATE daily_tasks SET last_completed = ?, enabled = 0 WHERE id = ?",
                (date.today().isoformat(), task_id)
            )
        else:
            next_due = self._calc_next_due(task["recurrence"])
            self.conn.execute(
                "UPDATE daily_tasks SET last_completed = ?, next_due = ? WHERE id = ?",
                (date.today().isoformat(), next_due, task_id)
            )
        self.conn.commit()

    def update_task(self, task_id: int, **kwargs) -> None:
        allowed = {"name", "priority", "recurrence", "enabled", "deadline", "project_id"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        if "recurrence" in fields:
            if fields["recurrence"] == "once" and fields.get("deadline"):
                fields["next_due"] = fields["deadline"]
            elif fields["recurrence"] == "once":
                fields["next_due"] = date.today().isoformat()
            else:
                fields["next_due"] = self._calc_next_due(fields["recurrence"])
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [task_id]
        self.conn.execute(
            f"UPDATE daily_tasks SET {set_clause} WHERE id = ?", values
        )
        self.conn.commit()

    def delete_task(self, task_id: int) -> None:
        self.conn.execute("DELETE FROM daily_tasks WHERE id = ?", (task_id,))
        self.conn.commit()

    def get_archived_tasks(self) -> list[dict]:
        """Get completed/archived daily tasks (enabled=0)."""
        rows = self.conn.execute(
            "SELECT * FROM daily_tasks WHERE enabled = 0 "
            "ORDER BY last_completed DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def restore_task(self, task_id: int) -> None:
        """Restore archived task — re-enable and set next_due to today."""
        self.conn.execute(
            "UPDATE daily_tasks SET enabled = 1, next_due = ? WHERE id = ?",
            (date.today().isoformat(), task_id)
        )
        self.conn.commit()

    def restore_project(self, project_id: int) -> None:
        """Restore archived project to Active status."""
        self.conn.execute(
            "UPDATE projects SET status = 'Active', closed_at = NULL, "
            "last_modified_at = ? WHERE id = ?",
            (datetime.now().isoformat(), project_id)
        )
        self.conn.commit()

    def get_tasks_by_project(self, project_id: int, include_archived: bool = False) -> list[dict]:
        """Get tasks linked to a specific project."""
        if include_archived:
            rows = self.conn.execute(
                "SELECT * FROM daily_tasks WHERE project_id = ? ORDER BY next_due ASC",
                (project_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM daily_tasks WHERE project_id = ? AND enabled = 1 "
                "ORDER BY next_due ASC", (project_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_task(self, task_id: int) -> Optional[dict]:
        """Get a single task by ID."""
        row = self.conn.execute(
            "SELECT * FROM daily_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── AI Digests ────────────────────────────────────────────

    def save_digest(self, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO ai_digests (content) VALUES (?)", (content,)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_latest_digest(self) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM ai_digests ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # ── Search Index ─────────────────────────────────────────

    def _index_content(self, source_type: str, source_id: int,
                       project_id: int, title: str, body: str) -> None:
        """Insert a row into the FTS5 search_index."""
        try:
            with self._write_lock:
                self.conn.execute(
                    "INSERT INTO search_index (source_type, source_id, project_id, title, body) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (source_type, str(source_id), str(project_id), title or "", body or "")
                )
                self.conn.commit()
        except Exception:
            logger.debug("Failed to index %s:%s", source_type, source_id)

    def _deindex_content(self, source_type: str, source_id: int) -> None:
        """Remove rows from the FTS5 search_index."""
        try:
            with self._write_lock:
                self.conn.execute(
                    "DELETE FROM search_index WHERE source_type = ? AND source_id = ?",
                    (source_type, str(source_id))
                )
                self.conn.commit()
        except Exception:
            logger.debug("Failed to deindex %s:%s", source_type, source_id)

    def _deindex_project(self, project_id: int) -> None:
        """Remove all index entries belonging to a project."""
        try:
            with self._write_lock:
                self.conn.execute(
                    "DELETE FROM search_index WHERE project_id = ?",
                    (str(project_id),)
                )
                self.conn.commit()
        except Exception:
            logger.debug("Failed to deindex project %s", project_id)

    def clear_search_index(self) -> None:
        """Delete all rows from the FTS5 search_index."""
        with self._write_lock:
            self.conn.execute("DELETE FROM search_index")
            self.conn.commit()

    def search_index_count(self) -> int:
        """Return number of rows in the search_index."""
        row = self.conn.execute("SELECT count(*) FROM search_index").fetchone()
        return row[0] if row else 0

    def fts5_search(self, query: str, limit: int = 100) -> list[dict]:
        """Run an FTS5 MATCH query with BM25 ranking.

        Returns list of dicts with: source_type, source_id, project_id,
        title, snippet, score.
        """
        # Sanitize the query for FTS5: escape double quotes, wrap terms
        sanitized = query.replace('"', '""')
        # Split into terms and wrap each in quotes for safety
        terms = sanitized.split()
        if not terms:
            return []
        fts_query = " ".join(f'"{t}"' for t in terms)
        try:
            rows = self.conn.execute(
                """SELECT source_type, source_id, project_id, title,
                          snippet(search_index, 4, '<mark>', '</mark>', '...', 40) as snippet,
                          bm25(search_index) as score
                   FROM search_index
                   WHERE search_index MATCH ?
                   ORDER BY score
                   LIMIT ?""",
                (fts_query, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.exception("FTS5 search failed for query: %s", query)
            return []

    def keyword_search(self, query: str, limit: int = 100) -> list[dict]:
        """Run a SQL LIKE keyword search across all content tables.

        Returns list of dicts with: source_type, source_id, project_id,
        title, snippet, score.
        """
        pattern = f"%{query}%"
        results = []

        # Projects
        for row in self.conn.execute(
            "SELECT id, id as project_id, title, description FROM projects "
            "WHERE title LIKE ? OR description LIKE ? OR whats_needed LIKE ? "
            "LIMIT ?", (pattern, pattern, pattern, limit)
        ).fetchall():
            r = dict(row)
            text = r.get("description", "")
            results.append({
                "source_type": "project", "source_id": r["id"],
                "project_id": r["project_id"], "title": r["title"],
                "snippet": self._make_snippet(text, query), "score": 0
            })

        # Emails
        for row in self.conn.execute(
            "SELECT id, project_id, subject, body_full, body_preview, sender, note "
            "FROM project_emails "
            "WHERE subject LIKE ? OR body_full LIKE ? OR body_preview LIKE ? "
            "OR sender LIKE ? OR note LIKE ? LIMIT ?",
            (pattern, pattern, pattern, pattern, pattern, limit)
        ).fetchall():
            r = dict(row)
            body = r.get("body_full") or r.get("body_preview", "")
            results.append({
                "source_type": "email", "source_id": r["id"],
                "project_id": r["project_id"], "title": r["subject"],
                "snippet": self._make_snippet(body, query), "score": 0
            })

        # Notes
        for row in self.conn.execute(
            "SELECT id, project_id, content FROM notes WHERE content LIKE ? LIMIT ?",
            (pattern, limit)
        ).fetchall():
            r = dict(row)
            results.append({
                "source_type": "note", "source_id": r["id"],
                "project_id": r["project_id"],
                "title": r["content"][:80],
                "snippet": self._make_snippet(r["content"], query), "score": 0
            })

        # Files
        for row in self.conn.execute(
            "SELECT pf.id, pf.project_id, pf.filename, pf.note, "
            "COALESCE(fc.content, '') as file_text "
            "FROM project_files pf LEFT JOIN file_content fc ON pf.id = fc.file_id "
            "WHERE pf.filename LIKE ? OR pf.note LIKE ? OR fc.content LIKE ? LIMIT ?",
            (pattern, pattern, pattern, limit)
        ).fetchall():
            r = dict(row)
            text = r.get("file_text") or r.get("note", "")
            results.append({
                "source_type": "file", "source_id": r["id"],
                "project_id": r["project_id"], "title": r["filename"],
                "snippet": self._make_snippet(text, query), "score": 0
            })

        # Daily tasks
        for row in self.conn.execute(
            "SELECT id, name FROM daily_tasks WHERE name LIKE ? AND enabled = 1 LIMIT ?",
            (pattern, limit)
        ).fetchall():
            r = dict(row)
            results.append({
                "source_type": "task", "source_id": r["id"],
                "project_id": 0, "title": r["name"],
                "snippet": r["name"], "score": 0
            })

        return results[:limit]

    @staticmethod
    def _make_snippet(text: str, query: str, context: int = 60) -> str:
        """Extract a snippet of text around the first occurrence of query."""
        if not text:
            return ""
        lower = text.lower()
        pos = lower.find(query.lower())
        if pos < 0:
            return text[:120] + ("..." if len(text) > 120 else "")
        start = max(0, pos - context)
        end = min(len(text), pos + len(query) + context)
        snippet = ""
        if start > 0:
            snippet += "..."
        snippet += text[start:end]
        if end < len(text):
            snippet += "..."
        return snippet

    # ── Bulk data access for reindexing ──────────────────────

    def get_all_projects_for_search(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, title, description, whats_needed, ai_summary FROM projects"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_emails_for_search(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, project_id, subject, sender, body_full, body_preview, "
            "note, stored_path FROM project_emails"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_notes_for_search(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, project_id, content FROM notes"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_files_for_search(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT pf.id, pf.project_id, pf.filename, pf.note, pf.stored_path, "
            "pf.file_type, COALESCE(fc.content, '') as cached_text "
            "FROM project_files pf LEFT JOIN file_content fc ON pf.id = fc.file_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_tasks_for_search(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name FROM daily_tasks WHERE enabled = 1"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── File content cache ───────────────────────────────────

    def get_file_content_cache(self, file_id: int) -> Optional[str]:
        row = self.conn.execute(
            "SELECT content FROM file_content WHERE file_id = ?", (file_id,)
        ).fetchone()
        return row["content"] if row else None

    def set_file_content_cache(self, file_id: int, content: str) -> None:
        with self._write_lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO file_content (file_id, content) VALUES (?, ?)",
                (file_id, content)
            )
            self.conn.commit()

    # ── Email body backfill ──────────────────────────────────

    def update_email_body_full(self, email_id: int, body_full: str) -> None:
        with self._write_lock:
            self.conn.execute(
                "UPDATE project_emails SET body_full = ? WHERE id = ?",
                (body_full, email_id)
            )
            self.conn.commit()

    # ── Embeddings (ML semantic search) ──────────────────────

    def save_embedding(self, source_type: str, source_id: int,
                       project_id: int, embedding: bytes) -> None:
        with self._write_lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO search_embeddings "
                "(source_type, source_id, project_id, embedding) VALUES (?, ?, ?, ?)",
                (source_type, source_id, project_id, embedding)
            )
            self.conn.commit()

    def get_all_embeddings(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT source_type, source_id, project_id, embedding "
            "FROM search_embeddings"
        ).fetchall()
        return [dict(r) for r in rows]

    def clear_embeddings(self) -> None:
        with self._write_lock:
            self.conn.execute("DELETE FROM search_embeddings")
            self.conn.commit()

    def embedding_count(self) -> int:
        row = self.conn.execute("SELECT count(*) FROM search_embeddings").fetchone()
        return row[0] if row else 0

    # ── Helpers ───────────────────────────────────────────────

    def _touch_project(self, project_id: int) -> None:
        self.conn.execute(
            "UPDATE projects SET last_modified_at = ? WHERE id = ?",
            (datetime.now().isoformat(), project_id)
        )

    @staticmethod
    def _calc_next_due(recurrence: str) -> str:
        from datetime import timedelta
        today = date.today()
        if recurrence == "once":
            return today.isoformat()
        elif recurrence == "daily":
            return (today + timedelta(days=1)).isoformat()
        elif recurrence == "weekly":
            return (today + timedelta(weeks=1)).isoformat()
        elif recurrence == "monthly":
            month = today.month % 12 + 1
            year = today.year + (1 if month == 1 else 0)
            day = min(today.day, 28)
            return date(year, month, day).isoformat()
        else:
            # Specific weekday
            weekday_map = {
                "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6
            }
            target = weekday_map.get(recurrence, 0)
            days_ahead = target - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return (today + timedelta(days=days_ahead)).isoformat()

    def get_project_text_for_ai(self, project_id: int) -> str:
        """Collect key text content from a project for AI summarization.

        Sends only: project metadata, user notes, and email subjects.
        Email bodies and file listings are excluded to keep context small.
        """
        project = self.get_project(project_id)
        if not project:
            return ""
        parts = []
        parts.append(f"Project: {project['title']}")
        if project["description"]:
            parts.append(f"Description: {project['description']}")
        if project["whats_needed"]:
            parts.append(f"What's needed to close: {project['whats_needed']}")
        parts.append(f"Priority: {project['priority']}")
        parts.append(f"Created: {project['created_at']}")
        if project["deadline"]:
            parts.append(f"Deadline: {project['deadline']}")

        notes = self.get_all_notes(project_id)
        if notes:
            parts.append("\nNotes:")
            for n in notes:
                parts.append(f"  [{n['created_at']}] {n['content']}")

        emails = self.get_emails(project_id)
        if emails:
            parts.append(f"\nEmails ({len(emails)} total):")
            for e in emails:
                parts.append(f"  [{e['email_date']}] {e['subject']}")

        return "\n".join(parts)
