# judge/utils.py

import tempfile
import shutil
import os
import logging

logger = logging.getLogger(__name__)

def create_workdir() -> str:
    """
    Create a temporary working directory with restricted permissions
    """
    try:
        workdir = tempfile.mkdtemp(prefix="judge_", dir="/tmp")
        # Set restrictive permissions (owner only)
        os.chmod(workdir, 0o700)
        return workdir
    except Exception as e:
        logger.error(f"Failed to create workdir: {e}")
        raise

def cleanup_workdir(path: str) -> None:
    """
    Safely remove working directory and all contents
    """
    try:
        if os.path.exists(path):
            # Force removal even if files are read-only
            shutil.rmtree(path, ignore_errors=True)
            logger.debug(f"Cleaned up workdir: {path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup workdir {path}: {e}")

def write_source(workdir: str, filename: str, content: str) -> str:
    """
    Write source code to file with security checks
    """
    try:
        # Prevent path traversal attacks
        if '..' in filename or '/' in filename:
            raise ValueError("Invalid filename")
        
        path = os.path.join(workdir, filename)
        
        # Ensure path is within workdir
        if not os.path.abspath(path).startswith(os.path.abspath(workdir)):
            raise ValueError("Path traversal attempt detected")
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # Set read-only permissions
        os.chmod(path, 0o400)
        
        return path
        
    except Exception as e:
        logger.error(f"Failed to write source: {e}")
        raise