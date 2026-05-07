"""Entry point for the Nuitka-built standalone executable.

Kept as a thin top-level wrapper so Nuitka has a clear single-file root to
compile from. The real CLI lives in bb_backup.cli:app.
"""

from bb_backup.cli import app

if __name__ == "__main__":
    app()
