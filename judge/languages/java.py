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
    MAX_PIDS,
)
from judge.utils import clean_error_message

logger = logging.getLogger(__name__)

SOURCE_FILE = "Main.java"
CLASS_FILE  = "Main"

_JVM_HEAP_MB = max(64, (MEMORY_LIMIT_BYTES // (1024 * 1024)) - 64)

import re as _re
_PUBLIC_CLASS_RE = _re.compile(r'\bpublic\s+class\s+(\w+)')

def _extract_class_name(source_code: str) -> str:
    """Return the public class name from source, defaulting to 'Main'."""
    m = _PUBLIC_CLASS_RE.search(source_code)
    return m.group(1) if m else "Main"


# ─── Compilation ──────────────────────────────────────────────────────────────

def compile(source_path: str, workdir: str):
    """
    Compile Java source with javac.

    Returns:
        (success: bool, error_message: str, class_name: str | None)
    """
    try:
        # Read source to find the actual public class name, then rename the file
        with open(source_path, "r", encoding="utf-8") as f:
            source_code = f.read()

        class_name = _extract_class_name(source_code)

        # javac requires the filename to match the public class name
        correct_path = os.path.join(workdir, f"{class_name}.java")
        if source_path != correct_path:
            os.rename(source_path, correct_path)
            source_path = correct_path

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

        class_file = os.path.join(workdir, f"{class_name}.class")
        if not os.path.exists(class_file):
            return False, "Compiler produced no .class output.", None

        return True, "", class_name

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
    # RLIMIT_AS intentionally omitted for JVM (large virtual address space at startup);
    # heap is capped via -Xmx instead. RLIMIT_AS would kill the JVM before it starts.
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PIDS,           MAX_PIDS))
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
                "PATH": "/usr/bin:/bin:/usr/local/bin",
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
