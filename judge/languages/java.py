# judge/languages/java.py

import subprocess
import os
import time
import resource
import logging
from judge.limits import (
    TIME_LIMIT_SEC,
    COMPILE_TIME_LIMIT_SEC,
    MEMORY_LIMIT_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_STDERR_BYTES,
    MAX_COMPILE_OUTPUT_KB,
)
from judge.utils import clean_error_message

logger = logging.getLogger(__name__)

SOURCE_FILE = "Main.java"
CLASS_FILE  = "Main"

# JVM memory cap derived from the global limit (convert bytes → MB, leave headroom for JVM overhead)
_JVM_HEAP_MB = max(64, (MEMORY_LIMIT_BYTES // (1024 * 1024)) - 64)


# ─── Compilation ──────────────────────────────────────────────────────────────

def compile(source_path: str, workdir: str):
    """
    Compile Java source with javac.

    Returns:
        (success: bool, error_message: str, class_name: str | None)
    """
    try:
        proc = subprocess.run(
            ["javac", "-encoding", "UTF-8", source_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMPILE_TIME_LIMIT_SEC,
            text=True,
            cwd=workdir,
        )

        if proc.returncode != 0:
            error = proc.stderr or proc.stdout or "Unknown compilation error"
            return False, clean_error_message(error, MAX_COMPILE_OUTPUT_KB * 1024), None

        class_file = os.path.join(workdir, f"{CLASS_FILE}.class")
        if not os.path.exists(class_file):
            return False, "Compiler produced no .class output.", None

        return True, "", CLASS_FILE

    except subprocess.TimeoutExpired:
        logger.warning("Java compilation timed out")
        return False, "Compilation timed out. Simplify your code.", None
    except FileNotFoundError:
        logger.error("javac not found on system")
        return False, "Java compiler (javac) is not available.", None
    except Exception as e:
        logger.error(f"Java compilation unexpected error: {e}", exc_info=True)
        return False, f"Compilation failed: {str(e)}", None


# ─── Execution ────────────────────────────────────────────────────────────────

def _apply_child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU,   (TIME_LIMIT_SEC + 2, TIME_LIMIT_SEC + 2))


def run(class_name: str, input_data: str, workdir: str) -> dict:
    """
    Execute compiled Java class for one test case.

    Returns dict with:
        ok               : bool
        verdict          : str
        output           : str   (if ok)
        error            : str   (if not ok)
        execution_time_ms: float
        memory_used_mb   : float
    """
    try:
        start_time = time.perf_counter()

        proc = subprocess.run(
            [
                "java",
                f"-Xmx{_JVM_HEAP_MB}m",
                f"-Xms16m",
                "-Djava.security.manager=disallow",  # disable SecurityManager API (Java 17+)
                "-cp", workdir,
                class_name,
            ],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIME_LIMIT_SEC,
            text=True,
            cwd=workdir,
            preexec_fn=_apply_child_limits,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": "/tmp",
                "JAVA_TOOL_OPTIONS": "",
            },
        )

        execution_time_ms = (time.perf_counter() - start_time) * 1000

        try:
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            memory_used_mb = usage.ru_maxrss / 1024
        except Exception:
            memory_used_mb = 0.0

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
            return _fail(
                "Output Limit Exceeded",
                "Your program produced too much output.",
                execution_time_ms,
                memory_used_mb,
            )

        if proc.returncode != 0:
            error_msg = _decode_java_error(proc.returncode, stderr)
            return _fail("Runtime Error", error_msg, execution_time_ms, memory_used_mb)

        return {
            "ok": True,
            "verdict": "Accepted",
            "output": stdout.strip(),
            "error": None,
            "execution_time_ms": round(execution_time_ms, 2),
            "memory_used_mb": round(max(memory_used_mb, 0.0), 2),
        }

    except subprocess.TimeoutExpired:
        return _fail(
            "Time Limit Exceeded",
            f"Your program exceeded the time limit of {TIME_LIMIT_SEC}s.",
            TIME_LIMIT_SEC * 1000,
            0.0,
        )
    except Exception as e:
        logger.error(f"Java run unexpected error: {e}", exc_info=True)
        return _fail("Runtime Error", str(e), 0.0, 0.0)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fail(verdict: str, error: str, time_ms: float, mem_mb: float) -> dict:
    return {
        "ok": False,
        "verdict": verdict,
        "output": None,
        "error": clean_error_message(error, MAX_STDERR_BYTES),
        "execution_time_ms": round(time_ms, 2),
        "memory_used_mb": round(max(mem_mb, 0.0), 2),
    }


def _decode_java_error(returncode: int, stderr: str) -> str:
    cleaned = clean_error_message(stderr, MAX_STDERR_BYTES)

    if "OutOfMemoryError" in stderr:
        return "Memory Limit Exceeded — your program ran out of heap memory."
    if "StackOverflowError" in stderr:
        return "Runtime Error — stack overflow (infinite recursion?)."
    if "Exception in thread" in stderr:
        # Surface the first Exception line — most useful for users
        for line in stderr.splitlines():
            if "Exception" in line or "Error" in line:
                return clean_error_message(line.strip(), MAX_STDERR_BYTES)

    if cleaned:
        return cleaned
    return f"Program exited with code {returncode}."
