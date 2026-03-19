# judge/languages/python.py

import subprocess
import os
import time
import resource
import logging
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

SOURCE_FILE = "main.py"

# Modules that are outright dangerous in a sandbox
_BLOCKED_IMPORTS = frozenset([
    "os", "sys", "subprocess", "socket", "shutil", "pathlib",
    "importlib", "ctypes", "multiprocessing", "threading",
    "signal", "pty", "tty", "termios", "fcntl", "resource",
    "gc", "inspect", "ast", "dis", "code", "codeop",
    "runpy", "zipimport", "pkgutil", "site",
])


# ─── Security preexec ─────────────────────────────────────────────────────────

def _apply_child_limits():
    """
    Applied via preexec_fn — runs inside the child before exec().
    """
    resource.setrlimit(resource.RLIMIT_AS,    (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_STACK, (STACK_LIMIT_BYTES,  STACK_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES,     MAX_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PIDS,           MAX_PIDS))
    resource.setrlimit(resource.RLIMIT_CPU,   (TIME_LIMIT_SEC + 1, TIME_LIMIT_SEC + 1))


# ─── Syntax check (compile phase) ────────────────────────────────────────────

def compile(source_path: str, workdir: str):
    """
    Validate Python syntax and check for blocked imports.

    Returns:
        (success: bool, error_message: str, source_path: str | None)
    """
    try:
        # 1. Syntax check via py_compile
        proc = subprocess.run(
            ["python3", "-m", "py_compile", source_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            text=True,
            cwd=workdir,
        )

        if proc.returncode != 0:
            error = proc.stderr or "Syntax error"
            return False, clean_error_message(error, MAX_STDERR_BYTES), None

        # 2. Static import check — parse AST without executing
        import ast as _ast
        try:
            with open(source_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = _ast.parse(source)
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                    names = (
                        [alias.name for alias in node.names]
                        if isinstance(node, _ast.Import)
                        else ([node.module] if node.module else [])
                    )
                    for name in names:
                        root = name.split(".")[0] if name else ""
                        if root in _BLOCKED_IMPORTS:
                            return (
                                False,
                                f"Import of '{root}' is not allowed in the sandbox.",
                                None,
                            )
        except _ast.SyntaxError:
            pass  # Already caught by py_compile above

        # 3. Write the runner wrapper once here — while workdir is still writable.
        #    run() will reuse this same file for every test case.
        wrapper_path = os.path.join(workdir, "_runner.py")
        with open(wrapper_path, "w", encoding="utf-8") as f:
            f.write(_WRAPPER_TEMPLATE.format(source_path=source_path))
        os.chmod(wrapper_path, 0o500)   # read+execute, not writable

        return True, "", source_path

    except subprocess.TimeoutExpired:
        return False, "Syntax check timed out.", None
    except Exception as e:
        logger.error(f"Python compile error: {e}", exc_info=True)
        return False, str(e), None


# ─── Execution ────────────────────────────────────────────────────────────────

# Minimal wrapper — injects memory + time telemetry via stderr markers,
# then runs user code in the same interpreter process (no nested exec).
# tracemalloc gives peak heap without subprocess overhead.
_WRAPPER_TEMPLATE = """\
import tracemalloc as _tm
import sys as _sys
import time as _time

_tm.start()
_t0 = _time.perf_counter()

try:
    with open({source_path!r}) as _f:
        exec(compile(_f.read(), {source_path!r}, 'exec'), {{}})
except SystemExit:
    pass
except Exception as _e:
    print(f"{{type(_e).__name__}}: {{_e}}", file=_sys.stderr)
    _sys.exit(1)
finally:
    _elapsed = (_time.perf_counter() - _t0) * 1000
    _cur, _peak = _tm.get_traced_memory()
    _tm.stop()
    print(f"__EXEC_TIME__{{_elapsed}}", file=_sys.stderr)
    print(f"__MEMORY__{{_peak / (1024 * 1024)}}", file=_sys.stderr)
"""


def run(source_path: str, input_data: str, workdir: str) -> dict:
    """
    Execute Python source for one test case with memory tracking and isolation.
    The _runner.py wrapper is written once during compile() and reused here.

    Returns dict with:
        ok               : bool
        verdict          : str
        output           : str   (if ok)
        error            : str   (if not ok — actual Python exception)
        execution_time_ms: float
        memory_used_mb   : float
    """
    # Wrapper was already written during compile() — just reference it
    wrapper_path = os.path.join(workdir, "_runner.py")

    try:
        start_wall = time.perf_counter()

        proc = subprocess.run(
            [
                "python3",
                "-B",               # No .pyc files
                "-S",               # No site.py (cleaner env)
                wrapper_path,
            ],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIME_LIMIT_SEC,
            text=True,
            cwd=workdir,
            preexec_fn=_apply_child_limits,
            env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "PYTHONHASHSEED": "0",      # Deterministic hashing
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            },
        )

        wall_ms = (time.perf_counter() - start_wall) * 1000

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # ── Extract telemetry from stderr markers ─────────────────────────
        execution_time_ms = wall_ms
        memory_used_mb = 0.0
        error_lines = []

        for line in stderr.splitlines():
            if line.startswith("__EXEC_TIME__"):
                try:
                    execution_time_ms = float(line[len("__EXEC_TIME__"):])
                except ValueError:
                    pass
            elif line.startswith("__MEMORY__"):
                try:
                    memory_used_mb = float(line[len("__MEMORY__"):])
                except ValueError:
                    pass
            else:
                error_lines.append(line)

        user_stderr = "\n".join(error_lines).strip()

        # ── Output size check ─────────────────────────────────────────────
        if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
            return _fail(
                "Output Limit Exceeded",
                "Your program produced too much output.",
                execution_time_ms,
                memory_used_mb,
            )

        # ── Runtime error check ───────────────────────────────────────────
        if proc.returncode != 0:
            # user_stderr contains the actual Python exception line
            if "MemoryError" in user_stderr:
                verdict = "Memory Limit Exceeded"
                msg = "Your program exceeded the memory limit."
            elif "RecursionError" in user_stderr:
                verdict = "Runtime Error"
                msg = user_stderr or "Maximum recursion depth exceeded."
            else:
                verdict = "Runtime Error"
                msg = user_stderr or f"Program exited with code {proc.returncode}."

            return _fail(verdict, msg, execution_time_ms, memory_used_mb)

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
        logger.error(f"Python run unexpected error: {e}", exc_info=True)
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