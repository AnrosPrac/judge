# judge/judge.py

import logging
from judge.utils import create_workdir, cleanup_workdir, write_source
from judge.limits import MAX_TESTCASES, MAX_SOURCE_SIZE_KB
from judge.languages import c as c_lang
from judge.languages import cpp as cpp_lang
from judge.languages import python as python_lang

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
    Strip and normalise line endings.
    Handles \\r\\n (Windows), \\r (old Mac), \\n (Unix).
    Prevents false Wrong Answers from line ending mismatches.
    """
    return text.strip().replace("\r\n", "\n").replace("\r", "\n")


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
                        "error":             None,
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