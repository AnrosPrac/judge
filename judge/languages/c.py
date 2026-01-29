# judge/languages/c.py - ENHANCED WITH TIMING & MEMORY TRACKING

import subprocess
import os
import time
import resource
from judge.limits import (
    TIME_LIMIT_SEC, 
    COMPILE_TIME_LIMIT_SEC,
    MEMORY_LIMIT_MB,
    MAX_OUTPUT_SIZE_KB
)
import logging

logger = logging.getLogger(__name__)

SOURCE_FILE = "main.c"
BINARY_FILE = "a.out"

def compile(source_path: str, workdir: str):
    """
    Compile C source code with security flags
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
                "-Werror=format-security",
                "-fstack-protector-strong",
                "-D_FORTIFY_SOURCE=2",
                "-o", binary_path
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMPILE_TIME_LIMIT_SEC,
            text=True,
            cwd=workdir
        )

        if proc.returncode != 0:
            error_msg = proc.stderr if proc.stderr else "Unknown compilation error"
            return False, error_msg, None

        # Verify binary was created
        if not os.path.exists(binary_path):
            return False, "Binary not generated", None

        return True, "", binary_path

    except subprocess.TimeoutExpired:
        logger.warning("C compilation timeout")
        return False, "Compilation Timeout", None
    except Exception as e:
        logger.error(f"C compilation error: {e}")
        return False, str(e), None


def run(binary_path: str, input_data: str, workdir: str):
    """
    Run compiled C binary with execution time and memory tracking
    
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

        # Start timing
        start_time = time.perf_counter()
        
        # Run process
        proc = subprocess.run(
            [binary_path],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIME_LIMIT_SEC,
            text=True,
            cwd=workdir
        )
        
        # Calculate execution time
        execution_time_ms = (time.perf_counter() - start_time) * 1000
        
        # Get memory usage (rough estimation using resource module)
        # Note: This gives us the parent process memory, not the child
        # For accurate child process memory, we'd need psutil or custom monitoring
        try:
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            memory_used_mb = usage.ru_maxrss / 1024  # Convert KB to MB (on Linux)
            # On macOS, ru_maxrss is in bytes, so we'd divide by (1024*1024)
            # For now, assuming Linux
        except:
            memory_used_mb = 0.0  # Fallback if resource tracking fails

        output = proc.stdout
        
        # Check output size
        if len(output.encode('utf-8')) > max_output_bytes:
            return {
                "ok": False,
                "verdict": "Output Limit Exceeded",
                "execution_time_ms": execution_time_ms,
                "memory_used_mb": memory_used_mb
            }

        # Runtime error check
        if proc.returncode != 0:
            return {
                "ok": False,
                "verdict": "Runtime Error",
                "execution_time_ms": execution_time_ms,
                "memory_used_mb": memory_used_mb
            }

        return {
            "ok": True,
            "output": output.strip(),
            "execution_time_ms": round(execution_time_ms, 2),
            "memory_used_mb": round(memory_used_mb, 2)
        }

    except subprocess.TimeoutExpired:
        logger.info("C execution timeout")
        return {
            "ok": False,
            "verdict": "Time Limit Exceeded",
            "execution_time_ms": TIME_LIMIT_SEC * 1000,  # Max time
            "memory_used_mb": 0.0
        }
    except Exception as e:
        logger.error(f"C execution error: {e}")
        return {
            "ok": False,
            "verdict": "Runtime Error",
            "execution_time_ms": 0.0,
            "memory_used_mb": 0.0
        }