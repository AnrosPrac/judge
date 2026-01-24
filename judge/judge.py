# judge/judge.py

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
    Judge a code submission with proper error handling and security
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
                "error": error.strip()[:1000]  # Limit error message size
            }

        # Execution phase
        passed = 0
        for idx, tc in enumerate(testcases):
            logger.debug(f"Running test case {idx + 1}/{len(testcases)}")
            
            result = lang["module"].run(executable, tc["input"], workdir)

            if not result["ok"]:
                logger.info(f"Test case {idx + 1} failed: {result['verdict']}")
                return {
                    "verdict": result["verdict"],
                    "passed": passed,
                    "total": len(testcases)
                }

            expected_output = tc["output"].strip()
            actual_output = result["output"].strip()

            if actual_output != expected_output:
                logger.info(f"Wrong answer on test case {idx + 1}")
                return {
                    "verdict": "Wrong Answer",
                    "passed": passed,
                    "total": len(testcases),
                    "expected": expected_output[:100],
                    "actual": actual_output[:100]
                }

            passed += 1

        logger.info(f"All test cases passed: {passed}/{len(testcases)}")
        return {
            "verdict": "Accepted",
            "passed": passed,
            "total": len(testcases)
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