# judge/languages/typescript.py
# Compile: tsc → main.js  |  Run: node main.js  (reuses JS executor logic)

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
    MAX_OUTPUT_BYTES,
    MAX_STDERR_BYTES,
    MAX_FILE_BYTES,
    MAX_PIDS,
    MAX_COMPILE_OUTPUT_KB,
)
from judge.utils import clean_error_message

logger = logging.getLogger(__name__)

SOURCE_FILE = "main.ts"
COMPILED_JS = "main.js"


# ─── Compilation ──────────────────────────────────────────────────────────────

def compile(source_path: str, workdir: str):
    """
    Transpile TypeScript to JS with tsc (no type-checking for speed).

    Returns:
        (success: bool, error_message: str, js_path: str | None)
    """
    js_path = os.path.join(workdir, COMPILED_JS)

    try:
        proc = subprocess.run(
            [
                "tsc",
                "--target", "ES2020",
                "--module", "commonjs",
                "--skipLibCheck",
                "--noEmitOnError",
                "--outDir", workdir,
                "--rootDir", workdir,
                source_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMPILE_TIME_LIMIT_SEC,
            text=True,
            cwd=workdir,
        )

        if proc.returncode != 0:
            error = proc.stdout or proc.stderr or "Unknown TypeScript compilation error"
            return False, clean_error_message(error, MAX_COMPILE_OUTPUT_KB * 1024), None

        if not os.path.exists(js_path):
            return False, "TypeScript compiler produced no output.", None

        return True, "", js_path

    except subprocess.TimeoutExpired:
        logger.warning("TypeScript compilation timed out")
        return False, "Compilation timed out. Simplify your code.", None
    except FileNotFoundError:
        logger.error("tsc not found on system")
        return False, "TypeScript compiler (tsc) is not available.", None
    except Exception as e:
        logger.error(f"TS compile error: {e}", exc_info=True)
        return False, f"Compilation failed: {str(e)}", None


# ─── Security preexec ─────────────────────────────────────────────────────────

def _apply_child_limits():
    # RLIMIT_AS omitted — same reason as javascript.py (V8 virtual address space).
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PIDS,       MAX_PIDS))
    resource.setrlimit(resource.RLIMIT_CPU,   (TIME_LIMIT_SEC + 1, TIME_LIMIT_SEC + 1))


# ─── Execution ────────────────────────────────────────────────────────────────

def run(js_path: str, input_data: str, workdir: str) -> dict:
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
                "--disallow-code-generation-from-strings",
                js_path,
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
        logger.error(f"TS run unexpected error: {e}", exc_info=True)
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
    for line in stderr.splitlines():
        line = line.strip()
        if line and not line.startswith("at ") and not line.startswith("(node:"):
            return clean_error_message(line, MAX_STDERR_BYTES)
    return clean_error_message(stderr, MAX_STDERR_BYTES) or f"Program exited with code {returncode}."
