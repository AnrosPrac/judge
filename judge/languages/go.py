# judge/languages/go.py

import subprocess
import os
import sys
import time
import logging

_IS_LINUX = sys.platform == "linux"
if _IS_LINUX:
    import resource  # kept for memory measurement in run()
from judge.limits import (
    TIME_LIMIT_SEC,
    COMPILE_TIME_LIMIT_SEC,
    MAX_OUTPUT_BYTES,
    MAX_STDERR_BYTES,
    MAX_COMPILE_OUTPUT_KB,
)
from judge.utils import clean_error_message

logger = logging.getLogger(__name__)

SOURCE_FILE = "main.go"


# ─── Compilation ──────────────────────────────────────────────────────────────

def compile(source_path: str, workdir: str):
    """
    Compile Go source to a static binary.

    Returns:
        (success: bool, error_message: str, binary_path: str | None)
    """
    # Go uses `go run` instead of compile+execute separately.
    # compile() just does a syntax check via `go vet`; the actual build+run
    # happens in run(). This avoids cold-cache binary linking (20-30s) entirely.
    # Return source_path as the "executable" — run() receives it and calls go run.
    try:
        proc = subprocess.run(
            ["go", "vet", source_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMPILE_TIME_LIMIT_SEC,
            text=True,
            cwd=workdir,
            env=_go_env(),
        )

        if proc.returncode != 0:
            error = proc.stderr or proc.stdout or "Syntax/type error"
            return False, clean_error_message(error, MAX_COMPILE_OUTPUT_KB * 1024), None

        return True, "", source_path

    except subprocess.TimeoutExpired:
        logger.warning("Go vet timed out")
        return False, "Compilation timed out.", None
    except FileNotFoundError:
        logger.error("go not found on system")
        return False, "Go compiler is not available.", None
    except Exception as e:
        logger.error(f"Go compile error: {e}", exc_info=True)
        return False, f"Compilation failed: {str(e)}", None


# ─── Shared env ──────────────────────────────────────────────────────────────

def _go_env() -> dict:
    return {
        "PATH":        "/usr/local/go/bin:/usr/bin:/bin:/usr/local/bin",
        "HOME":        "/tmp",
        "GOCACHE":     "/tmp/go_cache",   # persists across requests — warm cache = ~1s builds
        "GOPATH":      "/tmp/go_path",
        "CGO_ENABLED": "0",               # no C toolchain, static binary, faster
        "GO111MODULE": "off",             # GOPATH mode — no module graph, no network
    }


# ─── Execution ────────────────────────────────────────────────────────────────

def run(source_path: str, input_data: str, workdir: str) -> dict:
    try:
        start_time = time.perf_counter()
        try:
            _mem_before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss if _IS_LINUX else 0
        except Exception:
            _mem_before = 0

        # `go run` compiles + executes in one step using the incremental build cache.
        # On a warm GOCACHE this takes ~1s. Binary linking overhead is avoided entirely.
        # preexec_fn is NOT applied here — `go run` itself is the Go toolchain and needs
        # unrestricted address space during compilation. RLIMIT_AS would kill it before
        # user code ever runs. The subprocess timeout= is the wall-clock guard instead.
        proc = subprocess.run(
            ["go", "run", source_path],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIME_LIMIT_SEC + 10,  # extra headroom for first-run cache warm-up
            text=True,
            cwd=workdir,
            env=_go_env(),
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
            error_msg = _decode_go_error(proc.returncode, stderr)
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
            (TIME_LIMIT_SEC + 10) * 1000,
            0.0,
        )
    except Exception as e:
        logger.error(f"Go run unexpected error: {e}", exc_info=True)
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


def _decode_go_error(returncode: int, stderr: str) -> str:
    import signal as _signal

    cleaned = clean_error_message(stderr, MAX_STDERR_BYTES)

    if "runtime: out of memory" in stderr:
        return "Memory Limit Exceeded — your program ran out of memory."
    if "stack overflow" in stderr:
        return "Runtime Error — stack overflow (infinite recursion?)."
    if "panic:" in stderr:
        for line in stderr.splitlines():
            if line.startswith("panic:"):
                return clean_error_message(line.strip(), MAX_STDERR_BYTES)

    signal_messages = {
        _signal.SIGSEGV: "Segmentation fault — your program accessed invalid memory.",
        _signal.SIGFPE:  "Floating point exception — division by zero or overflow.",
        _signal.SIGABRT: "Program aborted.",
        _signal.SIGKILL: "Process killed — likely exceeded memory or process limit.",
        _signal.SIGXCPU: "CPU time limit exceeded.",
        _signal.SIGXFSZ: "Output file size limit exceeded.",
    }

    if returncode < 0:
        sig = -returncode
        try:
            sig_enum = _signal.Signals(sig)
            msg = signal_messages.get(sig_enum, f"Killed by signal {sig}.")
        except ValueError:
            msg = f"Killed by signal {sig}."
        return f"{msg}\n{cleaned}".strip() if cleaned else msg

    return cleaned or f"Program exited with code {returncode}."
