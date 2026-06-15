# judge/judge.py

import logging
from judge.utils import create_workdir, cleanup_workdir, write_source
from judge.limits import MAX_TESTCASES, MAX_SOURCE_SIZE_KB
from judge.languages import c as c_lang
from judge.languages import cpp as cpp_lang
from judge.languages import python as python_lang
from judge.languages import java as java_lang
from judge.languages import javascript as js_lang
from judge.languages import go as go_lang
from judge.languages import rust as rust_lang
from judge.languages import typescript as ts_lang
from judge.languages import kotlin as kotlin_lang
from judge.languages import ruby as ruby_lang
from judge.languages import php as php_lang

logger = logging.getLogger(__name__)

# ─── Language registry ────────────────────────────────────────────────────────

LANGUAGES = {
    "c": {
        "module":      c_lang,
        "source_name": "main.c",
    },
    "cpp": {
        "module":      cpp_lang,
        "source_name": "main.cpp",
    },
    "python": {
        "module":      python_lang,
        "source_name": "main.py",
    },
    "java": {
        "module":      java_lang,
        "source_name": "Main.java",
    },
    "javascript": {
        "module":      js_lang,
        "source_name": "main.js",
    },
    "go": {
        "module":      go_lang,
        "source_name": "main.go",
    },
    "rust": {
        "module":      rust_lang,
        "source_name": "main.rs",
    },
    "typescript": {
        "module":      ts_lang,
        "source_name": "main.ts",
    },
    "kotlin": {
        "module":      kotlin_lang,
        "source_name": "main.kt",
    },
    "ruby": {
        "module":      ruby_lang,
        "source_name": "main.rb",
    },
    "php": {
        "module":      php_lang,
        "source_name": "main.php",
    },
}

# ─── Verdict priority (lower index = higher priority for overall verdict) ─────
# When multiple test cases fail with different verdicts, we surface the
# most critical one as the overall verdict.
_VERDICT_PRIORITY = [
    "System Error",
    "Compilation Error",
    "Memory Limit Exceeded",
    "Time Limit Exceeded",
    "Output Limit Exceeded",
    "Runtime Error",
    "Wrong Answer",
    "Accepted",
]

def _verdict_rank(v: str) -> int:
    try:
        return _VERDICT_PRIORITY.index(v)
    except ValueError:
        return len(_VERDICT_PRIORITY)


# ─── Normalise text ───────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """
    Normalise line endings and strip trailing whitespace only.
    Handles \\r\\n (Windows), \\r (old Mac), \\n (Unix).
    Leading whitespace is preserved — problems may require it.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip()


# ─── Main entry point ─────────────────────────────────────────────────────────

def judge_submission(source_code: str, testcases: list, language: str) -> dict:
    """
    Judge a code submission against all test cases.

    Behaviour:
    - ALL test cases are always executed (no early exit on failure).
    - Per-test-case results include verdict, error message, timing, memory.
    - Overall verdict = highest-priority failing verdict across all test cases.
    - If all pass → "Accepted".

    Returns:
        {
            verdict          : str,
            passed           : int,
            total            : int,
            test_results     : List[TestResult],
            avg_execution_time_ms : float,
            max_execution_time_ms : float,
            avg_memory_mb    : float,
            max_memory_mb    : float,
            error            : str | None   (compilation error message)
        }
    """

    # ── Input validation ─────────────────────────────────────────────────────
    if language not in LANGUAGES:
        logger.warning(f"Unsupported language: {language!r}")
        return _system_error(f"Unsupported language: {language!r}")

    if not testcases:
        return _system_error("No test cases provided.")

    if len(testcases) > MAX_TESTCASES:
        return _system_error(
            f"Too many test cases: {len(testcases)} (max {MAX_TESTCASES})."
        )

    source_bytes = source_code.encode("utf-8")
    if len(source_bytes) > MAX_SOURCE_SIZE_KB * 1024:
        return _system_error(
            f"Source code too large ({len(source_bytes) // 1024} KB, max {MAX_SOURCE_SIZE_KB} KB)."
        )

    lang     = LANGUAGES[language]
    workdir  = None

    try:
        workdir      = create_workdir()
        source_path  = write_source(workdir, lang["source_name"], source_code)

        # ── Compilation ──────────────────────────────────────────────────────
        compiled, compile_error, executable = lang["module"].compile(source_path, workdir)

        if not compiled:
            logger.info(f"Compilation failed ({language}): {compile_error[:120]}")
            return {
                "verdict":              "Compilation Error",
                "passed":               0,
                "total":                len(testcases),
                "test_results":         [],
                "avg_execution_time_ms": 0.0,
                "max_execution_time_ms": 0.0,
                "avg_memory_mb":        0.0,
                "max_memory_mb":        0.0,
                "error":                compile_error,
            }

        # ── Run ALL test cases ───────────────────────────────────────────────
        test_results     = []
        passed           = 0
        execution_times  = []
        memory_usages    = []
        overall_verdict  = "Accepted"   # Optimistic — downgraded on failures

        for idx, tc in enumerate(testcases):
            tc_id    = idx + 1
            tc_input = _normalise(tc["input"])   # normalise input line endings

            logger.debug(f"Running test case {tc_id}/{len(testcases)}")

            result = lang["module"].run(executable, tc_input, workdir)

            exec_ms  = result.get("execution_time_ms", 0.0)
            mem_mb   = result.get("memory_used_mb",   0.0)
            execution_times.append(exec_ms)
            memory_usages.append(mem_mb)

            if not result["ok"]:
                # ── Failed test case ────────────────────────────────────────
                tc_verdict = result.get("verdict", "Runtime Error")
                tc_error   = result.get("error")   # actual error message

                test_results.append({
                    "test_case_id":      tc_id,
                    "passed":            False,
                    "verdict":           tc_verdict,
                    "error":             tc_error,
                    "output":            None,
                    "expected":          _safe_preview(tc["output"]),
                    "execution_time_ms": exec_ms,
                    "memory_used_mb":    mem_mb,
                })

                # Update overall verdict to highest-priority failure seen so far
                if _verdict_rank(tc_verdict) < _verdict_rank(overall_verdict):
                    overall_verdict = tc_verdict

                logger.debug(f"Test case {tc_id} failed: {tc_verdict}")
                # ← NO return here — continue to next test case

            else:
                # ── Check correctness ───────────────────────────────────────
                # normalise both sides — prevents false WA from \r\n vs \n
                expected = _normalise(tc["output"])
                actual   = _normalise(result["output"])

                if actual == expected:
                    passed += 1
                    test_results.append({
                        "test_case_id":      tc_id,
                        "passed":            True,
                        "verdict":           "Accepted",
                        "error":             None,
                        "output":            _safe_preview(actual),
                        "expected":          _safe_preview(expected),
                        "execution_time_ms": exec_ms,
                        "memory_used_mb":    mem_mb,
                    })
                else:
                    test_results.append({
                        "test_case_id":      tc_id,
                        "passed":            False,
                        "verdict":           "Wrong Answer",
                        "error":             f"Expected:\n{_safe_preview(expected)}\n\nGot:\n{_safe_preview(actual)}",
                        "output":            _safe_preview(actual),
                        "expected":          _safe_preview(expected),
                        "execution_time_ms": exec_ms,
                        "memory_used_mb":    mem_mb,
                    })

                    if _verdict_rank("Wrong Answer") < _verdict_rank(overall_verdict):
                        overall_verdict = "Wrong Answer"

                    logger.debug(f"Test case {tc_id}: Wrong Answer")

        # ── Aggregate metrics ─────────────────────────────────────────────
        n = len(execution_times)
        avg_time   = round(sum(execution_times) / n, 2) if n else 0.0
        max_time   = round(max(execution_times),    2) if n else 0.0
        avg_mem    = round(sum(memory_usages)    / n, 2) if n else 0.0
        max_mem    = round(max(memory_usages),       2) if n else 0.0

        logger.info(
            f"Judge complete | lang={language} verdict={overall_verdict} "
            f"passed={passed}/{len(testcases)} max_time={max_time}ms"
        )

        return {
            "verdict":               overall_verdict,
            "passed":                passed,
            "total":                 len(testcases),
            "test_results":          test_results,
            "avg_execution_time_ms": avg_time,
            "max_execution_time_ms": max_time,
            "avg_memory_mb":         avg_mem,
            "max_memory_mb":         max_mem,
            "error":                 None,
        }

    except Exception as e:
        logger.error(f"Judge system exception: {e}", exc_info=True)
        return _system_error(str(e))

    finally:
        if workdir:
            cleanup_workdir(workdir)


# ─── Online compiler (no testcase comparison) ────────────────────────────────

def run_submission(source_code: str, language: str, stdin: str = "") -> dict:
    """
    Compile and run code with raw stdin, return stdout/stderr directly.
    No verdict comparison — used by the /run (online compiler) endpoint.

    Returns:
        {
            stdout           : str,
            stderr           : str,
            execution_time_ms: float,
            memory_mb        : float,
            exit_code        : int
        }
    """
    if language not in LANGUAGES:
        return {"stdout": "", "stderr": f"Unsupported language: {language}", "execution_time_ms": 0.0, "memory_mb": 0.0, "exit_code": 1}

    source_bytes = source_code.encode("utf-8")
    if len(source_bytes) > MAX_SOURCE_SIZE_KB * 1024:
        return {"stdout": "", "stderr": f"Source code too large (max {MAX_SOURCE_SIZE_KB} KB).", "execution_time_ms": 0.0, "memory_mb": 0.0, "exit_code": 1}

    lang    = LANGUAGES[language]
    workdir = None

    try:
        workdir     = create_workdir()
        source_path = write_source(workdir, lang["source_name"], source_code)

        compiled, compile_error, executable = lang["module"].compile(source_path, workdir)
        if not compiled:
            return {
                "stdout": "",
                "stderr": compile_error,
                "execution_time_ms": 0.0,
                "memory_mb": 0.0,
                "exit_code": 1,
            }

        result = lang["module"].run(executable, _normalise(stdin), workdir)

        if result["ok"]:
            return {
                "stdout": result["output"],
                "stderr": "",
                "execution_time_ms": result["execution_time_ms"],
                "memory_mb": result["memory_used_mb"],
                "exit_code": 0,
            }
        else:
            # For TLE/MLE surface a clean message; for Runtime Error surface the actual error
            verdict = result.get("verdict", "Runtime Error")
            stderr_msg = result.get("error") or verdict
            return {
                "stdout": "",
                "stderr": stderr_msg,
                "execution_time_ms": result["execution_time_ms"],
                "memory_mb": result["memory_used_mb"],
                "exit_code": 1,
            }

    except Exception as e:
        logger.error(f"run_submission exception: {e}", exc_info=True)
        return {"stdout": "", "stderr": str(e), "execution_time_ms": 0.0, "memory_mb": 0.0, "exit_code": 1}

    finally:
        if workdir:
            cleanup_workdir(workdir)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _safe_preview(text: str, max_chars: int = 200) -> str:
    """Return a safe preview of output/expected for the response."""
    if not text:
        return ""
    preview = text[:max_chars]
    if len(text) > max_chars:
        preview += f"... ({len(text) - max_chars} more chars)"
    return preview


def _system_error(message: str) -> dict:
    return {
        "verdict":               "System Error",
        "passed":                0,
        "total":                 0,
        "test_results":          [],
        "avg_execution_time_ms": 0.0,
        "max_execution_time_ms": 0.0,
        "avg_memory_mb":         0.0,
        "max_memory_mb":         0.0,
        "error":                 message,
    }