# judge/languages/javascript.py

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
    MAX_OUTPUT_BYTES,
    MAX_STDERR_BYTES,
    MAX_FILE_BYTES,
    MAX_PIDS,
)
from judge.utils import clean_error_message

logger = logging.getLogger(__name__)

SOURCE_FILE = "main.js"

# Node.js has no compile step — compile() just validates syntax via --check
def compile(source_path: str, workdir: str):
    """
    Syntax-check JavaScript with node --check.

    Returns:
        (success: bool, error_message: str, source_path: str | None)
    """
    try:
        proc = subprocess.run(
            ["node", "--check", source_path],
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
        logger.error("node not found on system")
        return False, "Node.js runtime is not available.", None
    except Exception as e:
        logger.error(f"JS compile error: {e}", exc_info=True)
        return False, str(e), None


# ─── Security preexec ─────────────────────────────────────────────────────────

def _apply_child_limits():
    # RLIMIT_AS omitted — Node/V8 maps large virtual address ranges at startup;
    # memory is capped via --max-old-space-size instead.
    # RLIMIT_NPROC omitted — it counts against the OS user's total PID count, not
    # just this process. On a shared/containerised host Node's internal Worker Thread
    # scheduler hits the limit before running a single line of user code, causing an
    # immediate crash with a cryptic node_platform.cc abort (exit code 1, no stdout).
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU,   (TIME_LIMIT_SEC + 1, TIME_LIMIT_SEC + 1))


# ─── Execution ────────────────────────────────────────────────────────────────

def run(source_path: str, input_data: str, workdir: str) -> dict:
    try:
        start_time = time.perf_counter()
        try:
            _mem_before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss if _IS_LINUX else 0
        except Exception:
            _mem_before = 0

        proc = subprocess.run(
            [
                "node",
                "--max-old-space-size=200",
                source_path,
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
                "NODE_ENV": "production",
            },
        )

        execution_time_ms = (time.perf_counter() - start_time) * 1000

        try:
            _mem_after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss if _IS_LINUX else 0
            memory_used_mb = max(0.0, _mem_after - _mem_before) / 1024
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
            error_msg = _decode_node_error(proc.returncode, stderr)
            return _fail("Runtime Error", error_msg, execution_time_ms, memory_used_mb)

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
        logger.error(f"JS run unexpected error: {e}", exc_info=True)
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


def _decode_node_error(returncode: int, stderr: str) -> str:
    if "JavaScript heap out of memory" in stderr:
        return "Memory Limit Exceeded — your program ran out of heap memory."
    if "RangeError: Maximum call stack size exceeded" in stderr:
        return "Runtime Error — stack overflow (infinite recursion?)."

    # Surface the first useful error line (skip Node internals)
    for line in stderr.splitlines():
        line = line.strip()
        if line and not line.startswith("at ") and not line.startswith("(node:"):
            return clean_error_message(line, MAX_STDERR_BYTES)

    cleaned = clean_error_message(stderr, MAX_STDERR_BYTES)
    return cleaned or f"Program exited with code {returncode}."
