# judge/languages/python.py

import subprocess
import os
from judge.limits import (
    TIME_LIMIT_SEC,
    MAX_OUTPUT_SIZE_KB
)
import logging

logger = logging.getLogger(__name__)

SOURCE_FILE = "main.py"

def compile(source_path: str, workdir: str):
    """
    Validate Python syntax (no compilation needed)
    """
    try:
        # Just check syntax
        proc = subprocess.run(
            ["python3", "-m", "py_compile", source_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            text=True,
            cwd=workdir
        )

        if proc.returncode != 0:
            error_msg = proc.stderr if proc.stderr else "Syntax error"
            return False, error_msg, None

        return True, "", source_path

    except subprocess.TimeoutExpired:
        return False, "Syntax check timeout", None
    except Exception as e:
        logger.error(f"Python syntax check error: {e}")
        return False, str(e), None


def run(source_path: str, input_data: str, workdir: str):
    """
    Run Python code in restricted environment
    """
    try:
        max_output_bytes = MAX_OUTPUT_SIZE_KB * 1024

        # Run with restricted Python environment
        proc = subprocess.run(
            [
                "python3",
                "-S",  # Don't import site module (more restricted)
                "-B",  # Don't write .pyc files
                source_path
            ],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIME_LIMIT_SEC,
            text=True,
            cwd=workdir,
            env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "PATH": os.environ.get("PATH", "")
            }
        )

        output = proc.stdout
        
        if len(output.encode('utf-8')) > max_output_bytes:
            return {
                "ok": False,
                "verdict": "Output Limit Exceeded"
            }

        if proc.returncode != 0:
            stderr = proc.stderr
            # Distinguish between different error types
            if "MemoryError" in stderr:
                return {
                    "ok": False,
                    "verdict": "Memory Limit Exceeded"
                }
            return {
                "ok": False,
                "verdict": "Runtime Error"
            }

        return {
            "ok": True,
            "output": output.strip()
        }

    except subprocess.TimeoutExpired:
        logger.info("Python execution timeout")
        return {
            "ok": False,
            "verdict": "Time Limit Exceeded"
        }
    except Exception as e:
        logger.error(f"Python execution error: {e}")
        return {
            "ok": False,
            "verdict": "Runtime Error"
        }