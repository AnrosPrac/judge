# judge/judge.py - ENHANCED WITH PER-TEST-CASE METRICS

from judge.utils import create_workdir, cleanup_workdir, write_source
from judge.limits import MAX_TESTCASES, MAX_SOURCE_SIZE_KB
from judge.languages import c as c_lang
from judge.languages import cpp as cpp_lang
from judge.languages import python as python_lang
import logging

logger = logging.getLogger(__name__)

LANGUAGES = {
    "c": {
        "module": c_lang,
        "source_name": "main.c"
    },
    "cpp": {
        "module": cpp_lang,
        "source_name": "main.cpp"
    },
    "python": {
        "module": python_lang,
        "source_name": "main.py"
    }
}

def judge_submission(source_code: str, testcases: list, language: str):
    """
    Judge a code submission with per-test-case metrics
    
    Returns:
        dict with:
        - verdict: str (overall verdict)
        - passed: int (number of passed test cases)
        - total: int (total test cases)
        - test_results: list (per-test-case breakdown)
        - avg_execution_time_ms: float
        - max_execution_time_ms: float
        - avg_memory_mb: float
        - max_memory_mb: float
    """
    if language not in LANGUAGES:
        logger.warning(f"Unsupported language attempted: {language}")
        return {"verdict": "Unsupported Language"}

    if len(testcases) == 0 or len(testcases) > MAX_TESTCASES:
        logger.warning(f"Invalid testcase count: {len(testcases)}")
        return {"verdict": "Invalid Testcase Set"}

    if len(source_code.encode("utf-8")) > MAX_SOURCE_SIZE_KB * 1024:
        logger.warning(f"Source code too large: {len(source_code.encode('utf-8'))} bytes")
        return {"verdict": "Source Code Too Large"}

    lang = LANGUAGES[language]
    workdir = None

    try:
        workdir = create_workdir()
        logger.info(f"Created workdir: {workdir}")

        source_path = write_source(
            workdir,
            lang["source_name"],
            source_code
        )

        # Compilation phase
        compiled, error, executable = lang["module"].compile(source_path, workdir)

        if not compiled:
            logger.info(f"Compilation failed: {error[:100]}")
            return {
                "verdict": "Compilation Error",
                "error": error.strip()[:1000]
            }

        # Execution phase - track per-test-case results
        passed = 0
        test_results = []
        execution_times = []
        memory_usages = []

        for idx, tc in enumerate(testcases):
            logger.debug(f"Running test case {idx + 1}/{len(testcases)}")
            
            result = lang["module"].run(executable, tc["input"], workdir)

            # Extract metrics
            execution_time = result.get("execution_time_ms", 0.0)
            memory_used = result.get("memory_used_mb", 0.0)
            
            execution_times.append(execution_time)
            memory_usages.append(memory_used)

            # Check if test case failed
            if not result["ok"]:
                logger.info(f"Test case {idx + 1} failed: {result['verdict']}")
                
                # Add failed test case result
                test_results.append({
                    "test_case_id": idx + 1,
                    "passed": False,
                    "verdict": result["verdict"],
                    "execution_time_ms": execution_time,
                    "memory_used_mb": memory_used,
                    "output": None,
                    "expected": tc["output"][:100]
                })
                
                # Return early with aggregated metrics
                return {
                    "verdict": result["verdict"],
                    "passed": passed,
                    "total": len(testcases),
                    "test_results": test_results,
                    "avg_execution_time_ms": round(sum(execution_times) / len(execution_times), 2) if execution_times else 0,
                    "max_execution_time_ms": round(max(execution_times), 2) if execution_times else 0,
                    "avg_memory_mb": round(sum(memory_usages) / len(memory_usages), 2) if memory_usages else 0,
                    "max_memory_mb": round(max(memory_usages), 2) if memory_usages else 0
                }

            # Check output correctness
            expected_output = tc["output"].strip()
            actual_output = result["output"].strip()

            if actual_output != expected_output:
                logger.info(f"Wrong answer on test case {idx + 1}")
                
                test_results.append({
                    "test_case_id": idx + 1,
                    "passed": False,
                    "verdict": "Wrong Answer",
                    "execution_time_ms": execution_time,
                    "memory_used_mb": memory_used,
                    "output": actual_output[:100],
                    "expected": expected_output[:100]
                })
                
                return {
                    "verdict": "Wrong Answer",
                    "passed": passed,
                    "total": len(testcases),
                    "test_results": test_results,
                    "expected": expected_output[:100],
                    "actual": actual_output[:100],
                    "avg_execution_time_ms": round(sum(execution_times) / len(execution_times), 2) if execution_times else 0,
                    "max_execution_time_ms": round(max(execution_times), 2) if execution_times else 0,
                    "avg_memory_mb": round(sum(memory_usages) / len(memory_usages), 2) if memory_usages else 0,
                    "max_memory_mb": round(max(memory_usages), 2) if memory_usages else 0
                }

            # Test case passed!
            passed += 1
            test_results.append({
                "test_case_id": idx + 1,
                "passed": True,
                "verdict": "Accepted",
                "execution_time_ms": execution_time,
                "memory_used_mb": memory_used,
                "output": actual_output[:100],
                "expected": expected_output[:100]
            })

        # All test cases passed!
        logger.info(f"All test cases passed: {passed}/{len(testcases)}")
        
        return {
            "verdict": "Accepted",
            "passed": passed,
            "total": len(testcases),
            "test_results": test_results,
            "avg_execution_time_ms": round(sum(execution_times) / len(execution_times), 2) if execution_times else 0,
            "max_execution_time_ms": round(max(execution_times), 2) if execution_times else 0,
            "avg_memory_mb": round(sum(memory_usages) / len(memory_usages), 2) if memory_usages else 0,
            "max_memory_mb": round(max(memory_usages), 2) if memory_usages else 0
        }

    except Exception as e:
        logger.error(f"Judge exception: {e}", exc_info=True)
        return {
            "verdict": "System Error",
            "error": str(e)
        }

    finally:
        if workdir:
            cleanup_workdir(workdir)
            logger.debug(f"Cleaned up workdir: {workdir}")