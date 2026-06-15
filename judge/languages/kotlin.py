# judge/languages/kotlin.py
# Compile: kotlinc → main.jar  |  Run: java -jar main.jar

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

SOURCE_FILE = "main.kt"
JAR_FILE    = "main.jar"

_JVM_HEAP_MB = max(64, (MEMORY_LIMIT_BYTES // (1024 * 1024)) - 64)


# ─── Compilation ──────────────────────────────────────────────────────────────

def compile(source_path: str, workdir: str):
    """
    Compile Kotlin source to a self-contained JAR with kotlinc.

    Returns:
        (success: bool, error_message: str, jar_path: str | None)
    """
    jar_path = os.path.join(workdir, JAR_FILE)

    try:
        proc = subprocess.run(
            ["kotlinc", source_path, "-include-runtime", "-d", jar_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMPILE_TIME_LIMIT_SEC,
            text=True,
            cwd=workdir,
        )

        if proc.returncode != 0:
            error = proc.stderr or proc.stdout or "Unknown Kotlin compilation error"
            return False, clean_error_message(error, MAX_COMPILE_OUTPUT_KB * 1024), None

        if not os.path.exists(jar_path):
            return False, "Kotlin compiler produced no JAR output.", None

        return True, "", jar_path

    except subprocess.TimeoutExpired:
        logger.warning("Kotlin compilation timed out")
        return False, "Compilation timed out. Simplify your code.", None
    except FileNotFoundError:
        logger.error("kotlinc not found on system")
        return False, "Kotlin compiler (kotlinc) is not available.", None
    except Exception as e:
        logger.error(f"Kotlin compile error: {e}", exc_info=True)
        return False, f"Compilation failed: {str(e)}", None


# ─── Security preexec ─────────────────────────────────────────────────────────

def _apply_child_limits():
    # RLIMIT_AS intentionally omitted for JVM — see java.py for explanation.
    # RLIMIT_NPROC intentionally omitted — see java.py for explanation.
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU,   (TIME_LIMIT_SEC + 2, TIME_LIMIT_SEC + 2))


# ─── Execution ────────────────────────────────────────────────────────────────

def run(jar_path: str, input_data: str, workdir: str) -> dict:
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
                "-Xms16m",
                "-Djava.security.manager=disallow",
                "-jar", jar_path,
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
                "JAVA_TOOL_OPTIONS": "",
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
            verdict, error_msg = _decode_jvm_error(proc.returncode, stderr)
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
        logger.error(f"Kotlin run unexpected error: {e}", exc_info=True)
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


def _decode_jvm_error(returncode: int, stderr: str) -> tuple[str, str]:
    cleaned = clean_error_message(stderr, MAX_STDERR_BYTES)
    if "OutOfMemoryError" in stderr:
        return "Memory Limit Exceeded", "Your program ran out of heap memory."
    if "StackOverflowError" in stderr:
        return "Runtime Error", "Stack overflow — infinite recursion?"
    if "Exception in thread" in stderr:
        for line in stderr.splitlines():
            if "Exception" in line or "Error" in line:
                return "Runtime Error", clean_error_message(line.strip(), MAX_STDERR_BYTES)
    return "Runtime Error", cleaned or f"Program exited with code {returncode}."
