# judge/limits.py

# Execution limits
TIME_LIMIT_SEC = 2              # Per test case execution time
MEMORY_LIMIT_MB = 256           # Memory limit per process
CPU_LIMIT_PERCENT = 50          # CPU usage limit

# Testcase limits
MAX_TESTCASES = 10              # Maximum number of test cases
MAX_INPUT_SIZE_KB = 10          # Maximum input size per test case

# Source code limits
MAX_SOURCE_SIZE_KB = 100        # Maximum source code size

# Compilation limits
COMPILE_TIME_LIMIT_SEC = 10     # Compilation timeout

# Docker security limits
MAX_PIDS = 50                   # Maximum processes
MAX_FILE_SIZE_MB = 10           # Maximum file size that can be created
NETWORK_DISABLED = True         # Disable network access

# Output limits
MAX_OUTPUT_SIZE_KB = 10         # Maximum output size per test case