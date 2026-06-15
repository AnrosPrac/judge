# judge/languages/java.py

import subprocess
import os
import sys
import time
import logging

_IS_LINUX = sys.platform == "linux"
if _IS_LINUX:
    import resource
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
_ANY_CLASS_RE    = _re.compile(r'\bclass\s+(\w+)')

def _extract_class_name(source_code: str) -> str:
    """Return the public class name, falling back to first class, then 'Main'."""
    m = _PUBLIC_CLASS_RE.search(source_code) or _ANY_CLASS_RE.search(source_code)
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
    # RLIMIT_NPROC omitted — the JVM spawns GC, JIT, and signal-handler threads at
    # startup. On a shared/containerised host the per-user PID count is often already
    # near the OS limit; adding RLIMIT_NPROC causes the JVM to die before reaching
    # main(), producing "exit code 1" with no useful stderr.
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
        try:
            _mem_before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss if _IS_LINUX else 0
        except Exception:
            _mem_before = 0

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
            preexec_fn=_apply_child_limits if _IS_LINUX else None,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": "/tmp",
            },
        )

        execution_time_ms = (time.perf_counter() - start_time) * 1000

        try:
            _mem_after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss if _IS_LINUX else 0
            memory_used_mb = max(0.0, _mem_after - _mem_before) / 1024
        except Exception:
            memory_used_mb = 0.0

        stdout = proc.stdout or ""
        stderr = "\n".join(
            line for line in (proc.stderr or "").splitlines()
            if not line.startswith("Picked up ")
        )

        if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
            return _fail(
                "Output Limit Exceeded",
                "Your program produced too much output.",
                execution_time_ms,
                memory_used_mb,
            )

        if proc.returncode != 0:
            verdict, error_msg = _decode_java_error(proc.returncode, stderr)
            return _fail(verdict, error_msg, execution_time_ms, memory_used_mb)

        return {
            "ok": True,
            "verdict": "Accepted",
            "output": stdout.rstrip("\r\n"),
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


def _decode_java_error(returncode: int, stderr: str) -> tuple[str, str]:
    cleaned = clean_error_message(stderr, MAX_STDERR_BYTES)

    if "OutOfMemoryError" in stderr:
        return "Memory Limit Exceeded", "Your program ran out of heap memory."
    if "StackOverflowError" in stderr:
        return "Runtime Error", "Stack overflow — infinite recursion?"
    if "Exception in thread" in stderr:
        for line in stderr.splitlines():
            if "Exception" in line or "Error" in line:
                return "Runtime Error", clean_error_message(line.strip(), MAX_STDERR_BYTES)

    if cleaned:
        return "Runtime Error", cleaned
    return "Runtime Error", f"Program exited with code {returncode}."
