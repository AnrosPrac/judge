# judge/utils.py

import tempfile
import shutil
import os
import logging

logger = logging.getLogger(__name__)


def create_workdir() -> str:
    """
    Create a temporary working directory with restricted permissions.
    Uses /tmp which is typically a tmpfs mount (RAM-backed, fast, auto-cleaned).
    """
    try:
        workdir = tempfile.mkdtemp(prefix="judge_", dir="/tmp")
        os.chmod(workdir, 0o700)
        return workdir
    except Exception as e:
        logger.error(f"Failed to create workdir: {e}")
        raise


def cleanup_workdir(path: str) -> None:
    """
    Safely remove working directory and all contents.
    Never raises — cleanup failures are logged and swallowed.
    """
    try:
        if path and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
            logger.debug(f"Cleaned up workdir: {path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup workdir {path}: {e}")


def write_source(workdir: str, filename: str, content: str) -> str:
    """
    Write source code to file with path traversal protection.
    File is written read-only so the child process cannot modify its own source.
    """
    try:
        # Reject any path traversal attempts
        if ".." in filename or "/" in filename or "\\" in filename:
            raise ValueError(f"Invalid filename: {filename!r}")

        path = os.path.join(workdir, filename)

        # Double-check resolved path is inside workdir
        if not os.path.abspath(path).startswith(os.path.abspath(workdir) + os.sep):
            raise ValueError("Path traversal attempt detected")

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        # Read-only: process can read its source but not overwrite it
        os.chmod(path, 0o400)

        return path

    except Exception as e:
        logger.error(f"Failed to write source: {e}")
        raise


def truncate_output(text: str, max_bytes: int) -> tuple[str, bool]:
    """
    Truncate output to max_bytes, returning (truncated_text, was_truncated).
    Truncates on UTF-8 byte boundary to avoid splitting multi-byte characters.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, True


def clean_error_message(stderr: str, max_bytes: int) -> str:
    """
    Clean and truncate stderr for returning to the student.
    Strips internal file paths that expose server directory structure.
    """
    if not stderr:
        return ""

    lines = stderr.splitlines()
    cleaned = []
    for line in lines:
        # Strip absolute /tmp/judge_XXXX/ paths — expose only filename
        if "/tmp/judge_" in line:
            # Replace full path with just the filename portion
            import re
            line = re.sub(r"/tmp/judge_[^/]+/", "", line)
        cleaned.append(line)

    result = "\n".join(cleaned).strip()
    truncated, was_truncated = truncate_output(result, max_bytes)
    if was_truncated:
        truncated += "\n... (output truncated)"
    return truncated