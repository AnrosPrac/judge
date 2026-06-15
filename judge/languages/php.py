# judge/languages/php.py
# Syntax check via `php -l`, run via `php main.php`

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
    MEMORY_LIMIT_BYTES,
    STACK_LIMIT_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_STDERR_BYTES,
    MAX_FILE_BYTES,
    MAX_PIDS,
)
from judge.utils import clean_error_message

logger = logging.getLogger(__name__)

SOURCE_FILE = "main.php"

# Cap PHP memory via ini at runtime (php -d memory_limit=...)
_PHP_MEMORY_LIMIT_MB = max(32, (MEMORY_LIMIT_BYTES // (1024 * 1024)) - 32)


# ─── Compile (syntax check only) ─────────────────────────────────────────────

def compile(source_path: str, workdir: str):
    try:
        proc = subprocess.run(
            ["php", "-l", source_path],
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
        logger.error("php not found on system")
        return False, "PHP runtime is not available.", None
    except Exception as e:
        logger.error(f"PHP compile error: {e}", exc_info=True)
        return False, str(e), None


# ─── Security preexec ─────────────────────────────────────────────────────────

def _apply_child_limits():
    resource.setrlimit(resource.RLIMIT_AS,    (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_STACK, (STACK_LIMIT_BYTES,  STACK_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES,     MAX_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PIDS,           MAX_PIDS))
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
                "php",
                f"-d", f"memory_limit={_PHP_MEMORY_LIMIT_MB}M",
                "-d", "disable_functions=exec,passthru,shell_exec,system,proc_open,popen,curl_exec,curl_multi_exec,parse_ini_file,show_source",
                "-d", "allow_url_fopen=0",
                "-d", "allow_url_include=0",
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
        stderr = proc.stderr or ""

        if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
            return _fail(
                "Output Limit Exceeded",
                "Your program produced too much output.",
                execution_time_ms,
                memory_used_mb,
            )

        if proc.returncode != 0:
            error_msg = _decode_php_error(proc.returncode, stderr)
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
        logger.error(f"PHP run unexpected error: {e}", exc_info=True)
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


def _decode_php_error(returncode: int, stderr: str) -> str:
    cleaned = clean_error_message(stderr, MAX_STDERR_BYTES)
    if "Allowed memory size" in stderr:
        return "Memory Limit Exceeded — your program exceeded the memory limit."
    # PHP fatal/warning lines start with "PHP Fatal error:" or "PHP Warning:"
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith("PHP"):
            return clean_error_message(line, MAX_STDERR_BYTES)
    return cleaned or f"Program exited with code {returncode}."
