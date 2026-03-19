# judge/languages/c.py

import subprocess
import os
import time
import resource
import logging
from judge.limits import (
    TIME_LIMIT_SEC,
    COMPILE_TIME_LIMIT_SEC,
    MEMORY_LIMIT_BYTES,
    STACK_LIMIT_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_STDERR_BYTES,
    MAX_FILE_BYTES,
    MAX_PIDS,
    MAX_COMPILE_OUTPUT_KB,
)
from judge.utils import truncate_output, clean_error_message

logger = logging.getLogger(__name__)

SOURCE_FILE = "main.c"
BINARY_FILE = "a.out"


# ─── Security preexec ─────────────────────────────────────────────────────────

def _apply_child_limits():
    """
    Applied via preexec_fn — runs inside the child process before exec().
    Enforces hard resource limits that the kernel will kill on violation.
    These limits cannot be raised by the child process itself.
    """
    # Virtual memory limit (catches malloc bombs)
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))

    # Stack size limit
    resource.setrlimit(resource.RLIMIT_STACK, (STACK_LIMIT_BYTES, STACK_LIMIT_BYTES))

    # Max file size the process can create (prevents disk bombs)
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))

    # Max number of child processes (prevents fork bombs)
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PIDS, MAX_PIDS))

    # CPU time limit (backup to wall-clock timeout)
    resource.setrlimit(resource.RLIMIT_CPU, (TIME_LIMIT_SEC + 1, TIME_LIMIT_SEC + 1))


# ─── Compilation ──────────────────────────────────────────────────────────────

def compile(source_path: str, workdir: str):
    """
    Compile C source with security hardening flags.

    Returns:
        (success: bool, error_message: str, binary_path: str | None)
    """
    binary_path = os.path.join(workdir, BINARY_FILE)

    try:
        proc = subprocess.run(
            [
                "gcc",
                source_path,
                "-O2",
                "-std=c17",
                "-Wall",
                "-Wextra",
                "-Werror=format-security",   # Catch dangerous format strings
                "-fstack-protector-strong",  # Stack smashing protection
                "-D_FORTIFY_SOURCE=2",       # Buffer overflow detection
                "-pie", "-fPIE",             # Position independent executable
                "-o", binary_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMPILE_TIME_LIMIT_SEC,
            text=True,
            cwd=workdir,
        )

        if proc.returncode != 0:
            error = proc.stderr or proc.stdout or "Unknown compilation error"
            return False, clean_error_message(error, MAX_COMPILE_OUTPUT_KB * 1024), None

        if not os.path.exists(binary_path):
            return False, "Compiler produced no binary output.", None

        # Lock binary as executable but not writable
        os.chmod(binary_path, 0o500)

        return True, "", binary_path

    except subprocess.TimeoutExpired:
        logger.warning("C compilation timed out")
        return False, "Compilation timed out. Simplify your code.", None
    except FileNotFoundError:
        logger.error("gcc not found on system")
        return False, "C compiler (gcc) is not available.", None
    except Exception as e:
        logger.error(f"C compilation unexpected error: {e}", exc_info=True)
        return False, f"Compilation failed: {str(e)}", None


# ─── Execution ────────────────────────────────────────────────────────────────

def run(binary_path: str, input_data: str, workdir: str) -> dict:
    """
    Execute compiled C binary for one test case with full isolation.

    Returns dict with:
        ok               : bool
        verdict          : str
        output           : str   (if ok)
        error            : str   (if not ok — actual error message)
        execution_time_ms: float
        memory_used_mb   : float
    """
    try:
        start_time = time.perf_counter()

        proc = subprocess.run(
            [binary_path],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIME_LIMIT_SEC,
            text=True,
            cwd=workdir,
            preexec_fn=_apply_child_limits,   # ← kernel-enforced limits
            env={},                            # ← empty environment (no env leaks)
        )

        execution_time_ms = (time.perf_counter() - start_time) * 1000

        # Measure memory from child resource usage
        try:
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            # ru_maxrss is in KB on Linux
            memory_used_mb = usage.ru_maxrss / 1024
        except Exception:
            memory_used_mb = 0.0

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # ── Output size check ────────────────────────────────────────────────
        if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
            return _fail(
                "Output Limit Exceeded",
                "Your program produced too much output.",
                execution_time_ms,
                memory_used_mb,
            )

        # ── Runtime error check ──────────────────────────────────────────────
        if proc.returncode != 0:
            error_msg = _decode_exit_code(proc.returncode, stderr)
            return _fail(
                "Runtime Error",
                error_msg,
                execution_time_ms,
                memory_used_mb,
            )

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
    except MemoryError:
        return _fail("Memory Limit Exceeded", "Out of memory.", 0.0, 0.0)
    except Exception as e:
        logger.error(f"C run unexpected error: {e}", exc_info=True)
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


def _decode_exit_code(returncode: int, stderr: str) -> str:
    """
    Convert non-zero exit codes and signals to human-readable error messages.
    Negative return codes on Linux indicate signals (returncode = -signum).
    """
    import signal as _signal

    # Clean stderr first — use it if available
    cleaned_stderr = clean_error_message(stderr, MAX_STDERR_BYTES)

    signal_messages = {
        _signal.SIGSEGV: "Segmentation fault — your program accessed invalid memory.",
        _signal.SIGFPE:  "Floating point exception — division by zero or overflow.",
        _signal.SIGABRT: "Program aborted — assertion failed or abort() called.",
        _signal.SIGBUS:  "Bus error — misaligned memory access.",
        _signal.SIGILL:  "Illegal instruction — invalid CPU instruction executed.",
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
        if cleaned_stderr:
            return f"{msg}\n{cleaned_stderr}"
        return msg

    # Positive exit code
    if cleaned_stderr:
        return cleaned_stderr
    return f"Program exited with code {returncode}."