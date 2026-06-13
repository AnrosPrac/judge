# judge/languages/ruby.py
# Interpreted — syntax check via `ruby -c`, run via `ruby main.rb`

import subprocess
import os
import time
import resource
import logging
from judge.limits import (
    TIME_LIMIT_SEC,
    MAX_OUTPUT_BYTES,
    MAX_STDERR_BYTES,
    MAX_FILE_BYTES,
    MAX_PIDS,
)
from judge.utils import clean_error_message

logger = logging.getLogger(__name__)

SOURCE_FILE = "main.rb"


# ─── Compile (syntax check only) ─────────────────────────────────────────────

def compile(source_path: str, workdir: str):
    try:
        proc = subprocess.run(
            ["ruby", "-c", "-W2", source_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            text=True,
            cwd=workdir,
        )

        if proc.returncode != 0:
            error = proc.stderr or proc.stdout or "Syntax error"
            return False, clean_error_message(error, MAX_STDERR_BYTES), None

        return True, "", source_path

    except subprocess.TimeoutExpired:
        return False, "Syntax check timed out.", None
    except FileNotFoundError:
        logger.error("ruby not found on system")
        return False, "Ruby runtime is not available.", None
    except Exception as e:
        logger.error(f"Ruby compile error: {e}", exc_info=True)
        return False, str(e), None


# ─── Security preexec ─────────────────────────────────────────────────────────

def _apply_child_limits():
    # RLIMIT_AS omitted — Ruby runtime maps large VA ranges at startup (same issue as Node).
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PIDS,       MAX_PIDS))
    resource.setrlimit(resource.RLIMIT_CPU,   (TIME_LIMIT_SEC + 1, TIME_LIMIT_SEC + 1))


# ─── Execution ────────────────────────────────────────────────────────────────

def run(source_path: str, input_data: str, workdir: str) -> dict:
    try:
        start_time = time.perf_counter()

        proc = subprocess.run(
            ["ruby", source_path],
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
            error_msg = _decode_ruby_error(proc.returncode, stderr)
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
        logger.error(f"Ruby run unexpected error: {e}", exc_info=True)
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


def _decode_ruby_error(returncode: int, stderr: str) -> str:
    cleaned = clean_error_message(stderr, MAX_STDERR_BYTES)
    if "NoMemoryError" in stderr:
        return "Memory Limit Exceeded — your program ran out of memory."
    if "SystemStackError" in stderr:
        return "Runtime Error — stack overflow (infinite recursion?)."
    # Ruby errors are on the first non-blank line
    for line in stderr.splitlines():
        line = line.strip()
        if line:
            return clean_error_message(line, MAX_STDERR_BYTES)
    return cleaned or f"Program exited with code {returncode}."
