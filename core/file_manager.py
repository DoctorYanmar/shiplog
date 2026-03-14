"""File system management for ShipLog project folders.

Handles project folder creation, file copy/move, and path resolution.
"""

import os
import shutil
import platform
import subprocess
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_default_base_path() -> Path:
    return Path.home() / "ShipLog" / "projects"


def sanitize_name(name: str) -> str:
    """Sanitize a project name for use as a directory name."""
    clean = re.sub(r'[^\w\s-]', '', name).strip()
    clean = re.sub(r'[\s]+', '_', clean)
    return clean[:80] if clean else "unnamed"


class FileManager:
    """Manages project folder structure on disk."""

    def __init__(self, base_path: Optional[str] = None):
        if base_path:
            self.base_path = Path(base_path)
        else:
            self.base_path = get_default_base_path()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def create_project_folder(self, project_id: int, project_name: str) -> Path:
        """Create the folder structure for a new project."""
        folder_name = f"{project_id}_{sanitize_name(project_name)}"
        project_path = self.base_path / folder_name
        (project_path / "files").mkdir(parents=True, exist_ok=True)
        (project_path / "emails").mkdir(parents=True, exist_ok=True)
        logger.info("Created project folder: %s", project_path)
        return project_path

    def get_project_folder(self, project_id: int) -> Optional[Path]:
        """Find existing project folder by ID prefix."""
        for entry in self.base_path.iterdir():
            if entry.is_dir() and entry.name.startswith(f"{project_id}_"):
                return entry
        return None

    def ensure_project_folder(self, project_id: int, project_name: str) -> Path:
        """Get existing project folder or create one."""
        folder = self.get_project_folder(project_id)
        if folder and folder.exists():
            return folder
        return self.create_project_folder(project_id, project_name)

    def copy_file_to_project(self, source_path: str, project_id: int,
                             project_name: str) -> tuple[str, str]:
        """Copy a file into the project's files/ subfolder.

        Returns (filename, stored_path).
        """
        project_folder = self.ensure_project_folder(project_id, project_name)
        source = Path(source_path)
        dest_dir = project_folder / "files"
        dest = dest_dir / source.name

        # Handle duplicate names
        counter = 1
        while dest.exists():
            stem = source.stem
            suffix = source.suffix
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        shutil.copy2(str(source), str(dest))
        logger.info("Copied file %s -> %s", source, dest)
        return dest.name, str(dest)

    def copy_email_to_project(self, source_path: str, project_id: int,
                              project_name: str) -> tuple[str, str]:
        """Copy an email file into the project's emails/ subfolder.

        Returns (filename, stored_path).
        """
        project_folder = self.ensure_project_folder(project_id, project_name)
        source = Path(source_path)
        dest_dir = project_folder / "emails"
        dest = dest_dir / source.name

        counter = 1
        while dest.exists():
            stem = source.stem
            suffix = source.suffix
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        shutil.copy2(str(source), str(dest))
        logger.info("Copied email %s -> %s", source, dest)
        return dest.name, str(dest)

    def delete_stored_file(self, stored_path: str) -> bool:
        """Delete a file from disk."""
        path = Path(stored_path)
        if path.exists():
            path.unlink()
            logger.info("Deleted file: %s", path)
            return True
        return False

    def delete_project_folder(self, project_id: int) -> bool:
        """Delete the entire project folder from disk."""
        folder = self.get_project_folder(project_id)
        if folder and folder.exists():
            shutil.rmtree(str(folder))
            logger.info("Deleted project folder: %s", folder)
            return True
        return False

    @staticmethod
    def open_file(file_path: str) -> None:
        """Open a file with the OS default application."""
        path = Path(file_path)
        if not path.exists():
            logger.error("File not found: %s", path)
            return
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(str(path))
            elif system == "Darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            logger.exception("Failed to open file: %s", path)

    @staticmethod
    def open_folder(folder_path: str) -> None:
        """Open a folder in the OS file explorer."""
        path = Path(folder_path)
        if not path.exists():
            logger.error("Folder not found: %s", path)
            return
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(str(path))
            elif system == "Darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            logger.exception("Failed to open folder: %s", path)

    @staticmethod
    def get_file_type(filename: str) -> str:
        """Return a simple file type category from filename extension."""
        ext = Path(filename).suffix.lower()
        type_map = {
            ".pdf": "PDF",
            ".doc": "Word", ".docx": "Word",
            ".xls": "Excel", ".xlsx": "Excel",
            ".ppt": "PowerPoint", ".pptx": "PowerPoint",
            ".jpg": "Image", ".jpeg": "Image", ".png": "Image",
            ".gif": "Image", ".bmp": "Image",
            ".txt": "Text", ".csv": "Text", ".log": "Text",
            ".zip": "Archive", ".rar": "Archive", ".7z": "Archive",
            ".msg": "Email", ".eml": "Email",
        }
        return type_map.get(ext, "File")
