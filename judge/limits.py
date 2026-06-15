# judge/limits.py

# ─── Execution limits ────────────────────────────────────────────────────────
TIME_LIMIT_SEC          = 2       # Wall-clock time per test case
MEMORY_LIMIT_MB         = 256     # Virtual memory cap per child process
STACK_LIMIT_MB          = 64      # Stack size limit
CPU_LIMIT_PERCENT       = 50      # Informational only (enforced via cgroups in prod)

# ─── Output / input limits ───────────────────────────────────────────────────
MAX_OUTPUT_SIZE_KB      = 10      # stdout per test case
MAX_INPUT_SIZE_KB       = 10      # stdin per test case
MAX_STDERR_SIZE_KB      = 4       # stderr captured for error messages

# ─── Submission limits ───────────────────────────────────────────────────────
MAX_TESTCASES           = 20      # Maximum test cases per submission
MAX_SOURCE_SIZE_KB      = 100     # Maximum source code size

# ─── Compilation limits ──────────────────────────────────────────────────────
COMPILE_TIME_LIMIT_SEC  = 60      # Compilation timeout (kotlinc cold JVM start needs up to 45s)

# ─── Total request deadline ───────────────────────────────────────────────────
# Hard wall-clock budget for the entire /run request (compile + execute).
# Render free tier gateway kills HTTP connections at 30s. Stay under that so
# the judge returns a clean error to the client instead of a gateway timeout.
RUN_TOTAL_TIMEOUT_SEC   = 28
MAX_COMPILE_OUTPUT_KB   = 32      # Max compiler error output

# ─── Process limits (security) ───────────────────────────────────────────────
MAX_PIDS                = 32      # Max child processes (prevents fork bombs)
MAX_FILE_SIZE_MB        = 16      # Max file a process can create
NETWORK_DISABLED        = True    # Informational — enforce at infra level

# ─── Derived byte values (used internally) ───────────────────────────────────
MEMORY_LIMIT_BYTES      = MEMORY_LIMIT_MB  * 1024 * 1024
STACK_LIMIT_BYTES       = STACK_LIMIT_MB   * 1024 * 1024
MAX_OUTPUT_BYTES        = MAX_OUTPUT_SIZE_KB * 1024
MAX_STDERR_BYTES        = MAX_STDERR_SIZE_KB * 1024
MAX_FILE_BYTES          = MAX_FILE_SIZE_MB * 1024 * 1024