# judge/languages/python.py - ENHANCED WITH TIMING & MEMORY TRACKING

import subprocess
import os
import time
from judge.limits import (
    TIME_LIMIT_SEC,
    MAX_OUTPUT_SIZE_KB
)
import logging

logger = logging.getLogger(__name__)

SOURCE_FILE = "main.py"

def compile(source_path: str, workdir: str):
    """
    Validate Python syntax (no compilation needed)
    """
    try:
        # Just check syntax
        proc = subprocess.run(
            ["python3", "-m", "py_compile", source_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            text=True,
            cwd=workdir
        )

        if proc.returncode != 0:
            error_msg = proc.stderr if proc.stderr else "Syntax error"
            return False, error_msg, None

        return True, "", source_path

    except subprocess.TimeoutExpired:
        return False, "Syntax check timeout", None
    except Exception as e:
        logger.error(f"Python syntax check error: {e}")
        return False, str(e), None


def run(source_path: str, input_data: str, workdir: str):
    """
    Run Python code with execution time and memory tracking
    
    For Python, we create a wrapper script that uses tracemalloc to track memory
    
    Returns:
        dict with keys:
        - ok: bool
        - verdict: str
        - output: str (if successful)
        - execution_time_ms: float
        - memory_used_mb: float
    """
    try:
        max_output_bytes = MAX_OUTPUT_SIZE_KB * 1024

        # Create wrapper script for memory tracking
        wrapper_script = f"""
import tracemalloc
import sys
import time

# Start memory tracking
tracemalloc.start()
start_time = time.perf_counter()

# Execute user code
try:
    exec(open('{source_path}').read())
except Exception as e:
    print(f"RUNTIME_ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)

# Stop timing and memory tracking
elapsed = (time.perf_counter() - start_time) * 1000
current, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()

# Write metrics to stderr for extraction
print(f"__EXEC_TIME__{{elapsed}}", file=sys.stderr)
print(f"__MEMORY__{{peak / (1024 * 1024)}}", file=sys.stderr)
"""
        
        wrapper_path = os.path.join(workdir, "_wrapper.py")
        with open(wrapper_path, 'w') as f:
            f.write(wrapper_script)

        # Run wrapper script
        start_time = time.perf_counter()
        
        proc = subprocess.run(
            [
                "python3",
                "-B",  # Don't write .pyc files
                wrapper_path
            ],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIME_LIMIT_SEC,
            text=True,
            cwd=workdir,
            env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "PATH": os.environ.get("PATH", "")
            }
        )
        
        # Fallback timing if wrapper fails
        execution_time_ms = (time.perf_counter() - start_time) * 1000
        
        output = proc.stdout
        stderr = proc.stderr
        
        # Extract metrics from stderr
        memory_used_mb = 0.0
        extracted_time_ms = execution_time_ms
        
        for line in stderr.split('\n'):
            if line.startswith('__EXEC_TIME__'):
                try:
                    extracted_time_ms = float(line.replace('__EXEC_TIME__', ''))
                except:
                    pass
            elif line.startswith('__MEMORY__'):
                try:
                    memory_used_mb = float(line.replace('__MEMORY__', ''))
                except:
                    pass
        
        # Use extracted time if available, otherwise fallback
        if extracted_time_ms > 0:
            execution_time_ms = extracted_time_ms
        
        # Check output size
        if len(output.encode('utf-8')) > max_output_bytes:
            return {
                "ok": False,
                "verdict": "Output Limit Exceeded",
                "execution_time_ms": round(execution_time_ms, 2),
                "memory_used_mb": round(memory_used_mb, 2)
            }

        # Check for runtime errors
        if proc.returncode != 0:
            # Check if it's a memory error
            if "MemoryError" in stderr or "RUNTIME_ERROR" in stderr:
                verdict = "Memory Limit Exceeded" if "MemoryError" in stderr else "Runtime Error"
                return {
                    "ok": False,
                    "verdict": verdict,
                    "execution_time_ms": round(execution_time_ms, 2),
                    "memory_used_mb": round(memory_used_mb, 2)
                }
            return {
                "ok": False,
                "verdict": "Runtime Error",
                "execution_time_ms": round(execution_time_ms, 2),
                "memory_used_mb": round(memory_used_mb, 2)
            }

        return {
            "ok": True,
            "output": output.strip(),
            "execution_time_ms": round(execution_time_ms, 2),
            "memory_used_mb": round(memory_used_mb, 2)
        }

    except subprocess.TimeoutExpired:
        logger.info("Python execution timeout")
        return {
            "ok": False,
            "verdict": "Time Limit Exceeded",
            "execution_time_ms": TIME_LIMIT_SEC * 1000,
            "memory_used_mb": 0.0
        }
    except Exception as e:
        logger.error(f"Python execution error: {e}")
        return {
            "ok": False,
            "verdict": "Runtime Error",
            "execution_time_ms": 0.0,
            "memory_used_mb": 0.0
        }