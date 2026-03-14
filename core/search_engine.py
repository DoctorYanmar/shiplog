"""Search engine for ShipLog: FTS5 indexing, keyword search, and optional ML semantic search.

Provides background QThread workers for indexing and searching.
File text extraction from PDF, DOCX, and plain text files.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


# ── Data classes ─────────────────────────────────────────────

@dataclass
class SearchResult:
    """A single search result."""
    source_type: str     # 'project', 'email', 'note', 'file', 'file_content', 'task'
    source_id: int       # ID in source table
    project_id: int      # parent project (0 for tasks)
    project_title: str   # for display
    title: str           # result title
    snippet: str         # matched text with context
    score: float         # relevance score (BM25 or cosine)
    match_field: str = ""


# ── File text extraction ─────────────────────────────────────

def extract_text_from_file(file_path: str) -> str:
    """Extract text from PDF, DOCX, or plain text files.

    Returns extracted text or empty string on failure.
    """
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext in (".docx", ".doc"):
        return _extract_docx(file_path)
    elif ext in (".txt", ".csv", ".log"):
        return _extract_plaintext(file_path)
    return ""


def _extract_pdf(file_path: str) -> str:
    """Extract text from a PDF file using PyMuPDF."""
    try:
        import fitz
        doc = fitz.open(file_path)
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts)
    except ImportError:
        logger.debug("PyMuPDF not installed; PDF extraction unavailable")
        return ""
    except Exception:
        logger.debug("Failed to extract PDF text: %s", file_path)
        return ""


def _extract_docx(file_path: str) -> str:
    """Extract text from a DOCX file using python-docx."""
    try:
        from docx import Document
        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text)
    except ImportError:
        logger.debug("python-docx not installed; DOCX extraction unavailable")
        return ""
    except Exception:
        logger.debug("Failed to extract DOCX text: %s", file_path)
        return ""


def _extract_plaintext(file_path: str) -> str:
    """Read a plain text file."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(1_000_000)  # limit to 1MB
    except Exception:
        logger.debug("Failed to read text file: %s", file_path)
        return ""


def extract_email_full_body(file_path: str) -> str:
    """Re-parse an email file to get its full body text."""
    from shiplog.core.email_parser import parse_email
    parsed = parse_email(file_path)
    if parsed:
        return parsed.get("body_full", "")
    return ""


# ── Index Worker (QThread) ───────────────────────────────────

class IndexWorker(QThread):
    """Background worker that builds the full search index."""
    progress = pyqtSignal(int, int)        # current, total
    finished_indexing = pyqtSignal(int)     # total indexed
    status_message = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, db, file_manager=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.fm = file_manager

    def run(self):
        try:
            self._do_index()
        except Exception as e:
            logger.exception("Indexing failed")
            self.error_occurred.emit(str(e))

    def _do_index(self):
        self.status_message.emit("Clearing old index...")
        self.db.clear_search_index()

        total = 0

        # 1. Index projects
        self.status_message.emit("Indexing projects...")
        projects = self.db.get_all_projects_for_search()
        project_titles = {}
        for p in projects:
            project_titles[p["id"]] = p["title"]
            self.db._index_content(
                "project", p["id"], p["id"], p["title"],
                f"{p.get('description', '')} {p.get('whats_needed', '')} "
                f"{p.get('ai_summary', '')}"
            )
            total += 1

        # 2. Index emails (with body backfill)
        self.status_message.emit("Indexing emails...")
        emails = self.db.get_all_emails_for_search()
        for i, e in enumerate(emails):
            body = e.get("body_full", "")
            # Backfill: if body_full is empty, re-parse from file
            if not body and e.get("stored_path"):
                stored = e["stored_path"]
                if Path(stored).exists():
                    body = extract_email_full_body(stored)
                    if body:
                        self.db.update_email_body_full(e["id"], body)
            self.db._index_content(
                "email", e["id"], e["project_id"], e.get("subject", ""),
                f"{e.get('sender', '')} {body or e.get('body_preview', '')} "
                f"{e.get('note', '')}"
            )
            total += 1
            if (i + 1) % 50 == 0:
                self.progress.emit(total, total + len(emails) - i)

        # 3. Index notes
        self.status_message.emit("Indexing notes...")
        notes = self.db.get_all_notes_for_search()
        for n in notes:
            snippet = n["content"][:100] if len(n["content"]) > 100 else n["content"]
            self.db._index_content(
                "note", n["id"], n["project_id"], snippet, n["content"]
            )
            total += 1

        # 4. Index files (with text extraction)
        self.status_message.emit("Indexing files...")
        files = self.db.get_all_files_for_search()
        for i, f in enumerate(files):
            cached_text = f.get("cached_text", "")
            # Extract text from file if not cached
            if not cached_text and f.get("stored_path"):
                stored = f["stored_path"]
                if Path(stored).exists():
                    cached_text = extract_text_from_file(stored)
                    if cached_text:
                        self.db.set_file_content_cache(f["id"], cached_text)

            # Index file metadata
            self.db._index_content(
                "file", f["id"], f["project_id"],
                f["filename"], f.get("note", "")
            )
            total += 1

            # Index file content separately if it exists
            if cached_text:
                self.db._index_content(
                    "file_content", f["id"], f["project_id"],
                    f["filename"], cached_text
                )
                total += 1

            if (i + 1) % 20 == 0:
                self.status_message.emit(f"Extracting file text... ({i+1}/{len(files)})")

        # 5. Index daily tasks
        self.status_message.emit("Indexing tasks...")
        tasks = self.db.get_all_tasks_for_search()
        for t in tasks:
            self.db._index_content("task", t["id"], 0, t["name"], t["name"])
            total += 1

        self.status_message.emit(f"Index complete: {total} items")
        self.finished_indexing.emit(total)


# ── Search Worker (QThread) ──────────────────────────────────

class SearchWorker(QThread):
    """Background worker that executes a search query."""
    results_ready = pyqtSignal(list, str)   # [SearchResult], query
    error_occurred = pyqtSignal(str)

    def __init__(self, db, query: str, mode: str = "smart",
                 tfidf_engine=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.query = query.strip()
        self.mode = mode
        self.tfidf_engine = tfidf_engine

    def run(self):
        try:
            if self.mode == "smart":
                results = self._fts5_search()
            elif self.mode == "keyword":
                results = self._keyword_search()
            elif self.mode == "semantic":
                results = self._semantic_search()
            else:
                results = self._fts5_search()
            self.results_ready.emit(results, self.query)
        except Exception as e:
            logger.exception("Search failed")
            self.error_occurred.emit(str(e))

    def _fts5_search(self) -> list:
        """FTS5 MATCH with BM25 ranking."""
        raw = self.db.fts5_search(self.query)
        # Resolve project titles
        project_cache = {}
        results = []
        for row in raw:
            pid = int(row["project_id"])
            if pid not in project_cache:
                p = self.db.get_project(pid)
                project_cache[pid] = p["title"] if p else ""
            results.append(SearchResult(
                source_type=row["source_type"],
                source_id=int(row["source_id"]),
                project_id=pid,
                project_title=project_cache[pid],
                title=row["title"] or "",
                snippet=row["snippet"] or "",
                score=abs(float(row["score"])),
            ))
        return results

    def _keyword_search(self) -> list:
        """SQL LIKE keyword search."""
        raw = self.db.keyword_search(self.query)
        project_cache = {}
        results = []
        for row in raw:
            pid = int(row["project_id"])
            if pid not in project_cache:
                p = self.db.get_project(pid)
                project_cache[pid] = p["title"] if p else ""
            results.append(SearchResult(
                source_type=row["source_type"],
                source_id=int(row["source_id"]),
                project_id=pid,
                project_title=project_cache[pid],
                title=row["title"] or "",
                snippet=row["snippet"] or "",
                score=0,
            ))
        return results

    def _semantic_search(self) -> list:
        """ML semantic search using TF-IDF cosine similarity."""
        try:
            if not self.tfidf_engine:
                return self._fts5_search()
            raw = self.tfidf_engine.search(self.query)
            project_cache = {}
            results = []
            for score, row in raw:
                pid = int(row["project_id"])
                if pid not in project_cache:
                    p = self.db.get_project(pid)
                    project_cache[pid] = p["title"] if p else ""
                results.append(SearchResult(
                    source_type=row["source_type"],
                    source_id=int(row["source_id"]),
                    project_id=pid,
                    project_title=project_cache[pid],
                    title=row.get("title", ""),
                    snippet="",
                    score=score,
                ))
            return results
        except Exception:
            logger.exception("Semantic search failed, falling back to FTS5")
            return self._fts5_search()


# ── TF-IDF Search Engine (scikit-learn) ──────────────────────

class TfidfSearchEngine:
    """TF-IDF based semantic search using scikit-learn. No PyTorch needed."""

    def __init__(self):
        self.vectorizer = None
        self.tfidf_matrix = None
        self.items = []  # list of dicts with source_type, source_id, project_id, title

    @staticmethod
    def is_available() -> bool:
        """Check if scikit-learn is installed."""
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            return True
        except Exception:
            return False

    def build_index(self, items: list):
        """Build TF-IDF index from items.

        items: list of (source_type, source_id, project_id, title, text) tuples
        """
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.items = []
        texts = []
        for stype, sid, pid, title, text in items:
            combined = f"{title} {text}".strip()
            if not combined:
                continue
            self.items.append({
                "source_type": stype,
                "source_id": sid,
                "project_id": pid,
                "title": title,
            })
            texts.append(combined)

        if not texts:
            return

        self.vectorizer = TfidfVectorizer(
            max_features=10000,
            stop_words="english",
            sublinear_tf=True,
        )
        self.tfidf_matrix = self.vectorizer.fit_transform(texts)

    def search(self, query: str, limit: int = 50) -> list:
        """Search using cosine similarity against TF-IDF matrix."""
        if not self.vectorizer or self.tfidf_matrix is None:
            return []

        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np

        query_vec = self.vectorizer.transform([query])
        scores = cosine_similarity(query_vec, self.tfidf_matrix).flatten()

        top_indices = np.argsort(scores)[::-1][:limit]
        results = []
        for idx in top_indices:
            score = float(scores[idx])
            if score > 0.05:
                results.append((score, self.items[idx]))
        return results


# ── TF-IDF Build Worker (QThread) ────────────────────────────

class TfidfBuildWorker(QThread):
    """Background worker that builds a TF-IDF index for ML search."""
    status_message = pyqtSignal(str)
    finished_building = pyqtSignal(object)  # emits TfidfSearchEngine
    error_occurred = pyqtSignal(str)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db

    def run(self):
        try:
            engine = TfidfSearchEngine()
            if not engine.is_available():
                self.error_occurred.emit(
                    "scikit-learn not installed. "
                    "Install with: pip install scikit-learn"
                )
                return

            self.status_message.emit("Gathering content...")
            items = []

            for p in self.db.get_all_projects_for_search():
                text = (f"{p.get('description', '')} {p.get('whats_needed', '')} "
                        f"{p.get('ai_summary', '')}")
                items.append(("project", p["id"], p["id"], p["title"], text))

            for e in self.db.get_all_emails_for_search():
                body = e.get("body_full") or e.get("body_preview", "")
                text = f"{e.get('sender', '')} {body} {e.get('note', '')}"
                items.append(("email", e["id"], e["project_id"],
                              e.get("subject", ""), text))

            for n in self.db.get_all_notes_for_search():
                snippet = n["content"][:100] if len(n["content"]) > 100 else n["content"]
                items.append(("note", n["id"], n["project_id"],
                              snippet, n["content"]))

            for f in self.db.get_all_files_for_search():
                text = f"{f.get('note', '')} {f.get('cached_text', '')}"
                items.append(("file", f["id"], f["project_id"],
                              f["filename"], text))

            for t in self.db.get_all_tasks_for_search():
                items.append(("task", t["id"], 0, t["name"], t["name"]))

            self.status_message.emit(
                f"Building TF-IDF index ({len(items)} items)..."
            )
            engine.build_index(items)

            self.status_message.emit(f"ML index ready: {len(items)} items")
            self.finished_building.emit(engine)

        except Exception as e:
            logger.exception("TF-IDF build failed")
            self.error_occurred.emit(str(e))
