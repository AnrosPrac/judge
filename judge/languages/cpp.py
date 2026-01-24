# judge/languages/cpp.py

import subprocess
import os
from judge.limits import (
    TIME_LIMIT_SEC, 
    COMPILE_TIME_LIMIT_SEC,
    MEMORY_LIMIT_MB,
    MAX_OUTPUT_SIZE_KB
)
import logging

logger = logging.getLogger(__name__)

SOURCE_FILE = "main.cpp"
BINARY_FILE = "a.out"

def compile(source_path: str, workdir: str):
    """
    Compile C++ source code with security flags
    """
    binary_path = os.path.join(workdir, BINARY_FILE)

    try:
        proc = subprocess.run(
            [
                "g++", 
                source_path, 
                "-O2",
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-Werror=format-security",
                "-fstack-protector-strong",
                "-D_FORTIFY_SOURCE=2",
                "-o", binary_path
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMPILE_TIME_LIMIT_SEC,
            text=True,
            cwd=workdir
        )

        if proc.returncode != 0:
            error_msg = proc.stderr if proc.stderr else "Unknown compilation error"
            return False, error_msg, None

        if not os.path.exists(binary_path):
            return False, "Binary not generated", None

        return True, "", binary_path

    except subprocess.TimeoutExpired:
        logger.warning("C++ compilation timeout")
        return False, "Compilation Timeout", None
    except Exception as e:
        logger.error(f"C++ compilation error: {e}")
        return False, str(e), None


def run(binary_path: str, input_data: str, workdir: str):
    """
    Run compiled C++ binary in sandboxed environment
    """
    try:
        max_output_bytes = MAX_OUTPUT_SIZE_KB * 1024

        proc = subprocess.run(
            [binary_path],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIME_LIMIT_SEC,
            text=True,
            cwd=workdir
        )

        output = proc.stdout
        
        if len(output.encode('utf-8')) > max_output_bytes:
            return {
                "ok": False,
                "verdict": "Output Limit Exceeded"
            }

        if proc.returncode != 0:
            return {
                "ok": False,
                "verdict": "Runtime Error"
            }

        return {
            "ok": True,
            "output": output.strip()
        }

    except subprocess.TimeoutExpired:
        logger.info("C++ execution timeout")
        return {
            "ok": False,
            "verdict": "Time Limit Exceeded"
        }
    except Exception as e:
        logger.error(f"C++ execution error: {e}")
        return {
            "ok": False,
            "verdict": "Runtime Error"
        }