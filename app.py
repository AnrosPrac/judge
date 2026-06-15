# app.py - Single Service Architecture (Render Free Tier Compatible)

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from typing import List, Dict, Optional
import logging
import asyncio
import uuid
from datetime import datetime, timezone
from judge.judge import judge_submission, run_submission, LANGUAGES
from judge.limits import RUN_TOTAL_TIMEOUT_SEC
from judge.frontend.pixel_compare import compare_images
from judge.frontend.css_checker import check_css
from judge.frontend.html_checker import check_html_structure
from judge.frontend.responsive_checker import check_responsive
from judge.frontend.a11y_checker import check_accessibility
from judge.frontend.watermark import generate_token, verify_watermark
from auth import verify_api_key, rate_limiter
from config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ─── Shared state ─────────────────────────────────────────────────────────────

tasks: Dict[str, Dict] = {}
task_queue: asyncio.Queue = None
run_semaphore: asyncio.Semaphore = None


# ─── Lifespan ─────────────────────────────────────────────────────────────────

def _warmup_go_cache():
    """
    Run a trivial Go program at startup to populate GOCACHE with stdlib packages.
    Without this, the first user request pays the full 20-30s cold-compile cost.
    Runs in a thread so it doesn't block the event loop.
    """
    import subprocess, os, tempfile
    hello = 'package main\nimport "fmt"\nfunc main(){fmt.Println("ok")}\n'
    try:
        with tempfile.TemporaryDirectory(prefix="go_warmup_", dir="/tmp") as d:
            src = os.path.join(d, "main.go")
            with open(src, "w") as f:
                f.write(hello)
            env = {
                "PATH":        "/usr/local/go/bin:/usr/bin:/bin:/usr/local/bin",
                "HOME":        "/tmp",
                "GOCACHE":     "/tmp/go_cache",
                "GOPATH":      "/tmp/go_path",
                "CGO_ENABLED": "0",
                "GO111MODULE": "off",
            }
            os.makedirs("/tmp/go_cache", exist_ok=True)
            result = subprocess.run(
                ["go", "run", src],
                env=env, capture_output=True, timeout=60, cwd=d,
            )
            if result.returncode == 0:
                logger.info("Go build cache warmed up successfully")
            else:
                logger.warning(f"Go warmup failed: {result.stderr.decode()[:200]}")
    except Exception as e:
        logger.warning(f"Go cache warmup error (non-fatal): {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global task_queue, run_semaphore

    task_queue   = asyncio.Queue(maxsize=settings.MAX_QUEUE_SIZE)
    run_semaphore = asyncio.Semaphore(settings.MAX_RUN_CONCURRENT)

    workers = [asyncio.create_task(process_queue()) for _ in range(settings.MAX_CONCURRENT_TASKS)]
    cleanup = asyncio.create_task(_cleanup_loop())

    # Pre-warm Go's build cache so the first submission doesn't pay cold-compile cost
    asyncio.create_task(asyncio.to_thread(_warmup_go_cache))

    logger.info(
        f"Started {settings.MAX_CONCURRENT_TASKS} judge workers, "
        f"{settings.MAX_RUN_CONCURRENT} run slots, "
        f"queue capacity {settings.MAX_QUEUE_SIZE}"
    )

    yield

    for w in workers:
        w.cancel()
    cleanup.cancel()


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.SERVICE_NAME,
    version=settings.VERSION,
    docs_url="/docs" if settings.LOG_LEVEL == "DEBUG" else None,
    redoc_url="/redoc" if settings.LOG_LEVEL == "DEBUG" else None,
    lifespan=lifespan,
)

if settings.ENABLE_CORS:
    origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["POST", "GET"],
        allow_headers=["*"],
    )


# ─── Request models ───────────────────────────────────────────────────────────

_ALLOWED_LANGUAGES = list(LANGUAGES.keys())


class TestCase(BaseModel):
    input: str
    output: str

    @field_validator('input', 'output')
    @classmethod
    def validate_length(cls, v: str) -> str:
        if len(v) > 10000:
            raise ValueError('Input/output too large')
        return v


class JudgeRequest(BaseModel):
    language: str
    sourceCode: str
    testcases: List[TestCase]

    @field_validator('language')
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v.lower() not in _ALLOWED_LANGUAGES:
            raise ValueError(f'Language must be one of: {_ALLOWED_LANGUAGES}')
        return v.lower()

    @field_validator('testcases')
    @classmethod
    def validate_testcases(cls, v: list) -> list:
        if len(v) < 1 or len(v) > 20:
            raise ValueError('Must have 1-20 test cases')
        return v

    @field_validator('sourceCode')
    @classmethod
    def validate_source(cls, v: str) -> str:
        if len(v.encode('utf-8')) > 100 * 1024:
            raise ValueError('Source code too large')
        return v


class RunRequest(BaseModel):
    language: str
    sourceCode: str
    stdin: Optional[str] = ""

    @field_validator('language')
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v.lower() not in _ALLOWED_LANGUAGES:
            raise ValueError(f'Language must be one of: {_ALLOWED_LANGUAGES}')
        return v.lower()

    @field_validator('sourceCode')
    @classmethod
    def validate_source(cls, v: str) -> str:
        if len(v.encode('utf-8')) > 100 * 1024:
            raise ValueError('Source code too large')
        return v

    @field_validator('stdin')
    @classmethod
    def validate_stdin(cls, v: Optional[str]) -> str:
        if v and len(v.encode('utf-8')) > 10 * 1024:
            raise ValueError('stdin too large (max 10KB)')
        return v or ""


# ─── Global exception handler ─────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"verdict": "Internal Server Error", "error": "An unexpected error occurred"}
    )


# ─── Judge queue worker ───────────────────────────────────────────────────────

async def process_queue():
    logger.info("Judge queue worker started")
    while True:
        try:
            task_id, source_code, testcases, language = await task_queue.get()
            logger.info(f"Processing task {task_id}")

            tasks[task_id]["status"] = "processing"
            tasks[task_id]["message"] = "Executing submission..."

            try:
                result = await asyncio.to_thread(
                    judge_submission,
                    source_code=source_code,
                    testcases=testcases,
                    language=language,
                )
                tasks[task_id]["status"] = "completed"
                tasks[task_id]["result"] = result
                tasks[task_id]["completed_at"] = _now()
                logger.info(f"Task {task_id} completed: {result.get('verdict')}")

            except Exception as e:
                logger.error(f"Task {task_id} failed: {e}", exc_info=True)
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["result"] = {
                    "verdict": "System Error",
                    "passed": 0,
                    "total": 0,
                    "test_results": [],
                    "avg_execution_time_ms": 0.0,
                    "max_execution_time_ms": 0.0,
                    "avg_memory_mb": 0.0,
                    "max_memory_mb": 0.0,
                    "error": str(e),
                }

            task_queue.task_done()

        except Exception as e:
            logger.error(f"Queue processor error: {e}", exc_info=True)
            await asyncio.sleep(1)


async def _cleanup_loop():
    """Delete completed/failed tasks older than 1 hour."""
    while True:
        await asyncio.sleep(3600)
        now = datetime.now(timezone.utc)
        to_delete = [
            tid for tid, t in tasks.items()
            if t["status"] in ("completed", "failed")
            and (now - datetime.fromisoformat(t["created_at"])).total_seconds() > 3600
        ]
        for tid in to_delete:
            del tasks[tid]
        if to_delete:
            logger.info(f"Cleaned up {len(to_delete)} old tasks")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/judge")
async def judge(req: JudgeRequest, api_key: str = Depends(verify_api_key)):
    """Submit code for async judgment. Returns task_id immediately."""
    try:
        if task_queue.qsize() >= settings.MAX_QUEUE_SIZE:
            return JSONResponse(
                status_code=503,
                content={"status": "error", "error": "Queue is full. Please try again later."}
            )

        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            "status": "queued",
            "message": "Submission queued for execution",
            "created_at": _now(),
            "language": req.language,
            "result": None,
        }

        await task_queue.put((
            task_id,
            req.sourceCode,
            [tc.model_dump() for tc in req.testcases],
            req.language,
        ))

        logger.info(f"Task {task_id} queued (queue size: {task_queue.qsize()})")

        remaining = rate_limiter.get_remaining(api_key)
        return JSONResponse(
            content={
                "status": "queued",
                "task_id": task_id,
                "message": "Submission queued. Poll /status/{task_id} for results.",
                "queue_position": task_queue.qsize(),
            },
            headers={
                "X-RateLimit-Limit": str(settings.RATE_LIMIT_REQUESTS),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset": str(settings.RATE_LIMIT_WINDOW),
            }
        )

    except Exception as e:
        logger.error(f"Judge error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "error": str(e)})


@app.get("/status/{task_id}")
async def get_status(task_id: str, _: str = Depends(verify_api_key)):
    """Poll the result of a submitted judge task."""
    if task_id not in tasks:
        raise HTTPException(
            status_code=404,
            detail="Task not found. It may have expired or the service was restarted."
        )

    task = tasks[task_id]
    response = {
        "status": task["status"],
        "message": task.get("message", ""),
        "created_at": task.get("created_at"),
    }

    if task["status"] in ("completed", "failed"):
        response["result"] = task["result"]
        if task["status"] == "completed":
            response["completed_at"] = task.get("completed_at")

    return response


@app.post("/run")
async def run(req: RunRequest, _: str = Depends(verify_api_key)):
    """
    Compile and run code with optional stdin. Returns output immediately.
    Uses a separate concurrency pool — never blocks /judge workers.
    """
    async with run_semaphore:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    run_submission,
                    source_code=req.sourceCode,
                    language=req.language,
                    stdin=req.stdin,
                ),
                timeout=RUN_TOTAL_TIMEOUT_SEC,
            )
            return JSONResponse(content=result)
        except asyncio.TimeoutError:
            logger.warning(f"Run request timed out after {RUN_TOTAL_TIMEOUT_SEC}s ({req.language})")
            return JSONResponse(
                status_code=408,
                content={
                    "stdout": "",
                    "stderr": f"Execution timed out (compile + run exceeded {RUN_TOTAL_TIMEOUT_SEC}s).",
                    "execution_time_ms": RUN_TOTAL_TIMEOUT_SEC * 1000.0,
                    "memory_mb": 0.0,
                    "exit_code": 1,
                }
            )
        except Exception as e:
            logger.error(f"Run error: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "stdout": "",
                    "stderr": str(e),
                    "execution_time_ms": 0.0,
                    "memory_mb": 0.0,
                    "exit_code": 1,
                }
            )


@app.get("/health")
async def health():
    """Health check with live capacity metrics."""
    return {
        "status": "healthy",
        "queue_size": task_queue.qsize(),
        "queue_capacity": settings.MAX_QUEUE_SIZE,
        "judge_workers": settings.MAX_CONCURRENT_TASKS,
        "run_slots": settings.MAX_RUN_CONCURRENT,
        "active_tasks": len([t for t in tasks.values() if t["status"] == "processing"]),
        "total_tasks": len(tasks),
    }


@app.get("/")
async def root():
    return {
        "service": settings.SERVICE_NAME,
        "version": settings.VERSION,
        "supported_languages": _ALLOWED_LANGUAGES,
        "status": "online",
        "queue_size": task_queue.qsize() if task_queue else 0,
    }


# ─── Frontend evaluation models ───────────────────────────────────────────────

_MAX_HTML_BYTES   = 200 * 1024   # 200 KB
_MAX_CSS_BYTES    = 100 * 1024   # 100 KB
_MAX_IMAGE_BYTES  = 5 * 1024 * 1024  # 5 MB base64-encoded
_MAX_TESTS        = 100


class FrontendEvalRequest(BaseModel):
    html_code:        str
    css_code:         str
    screenshot_b64:   str
    reference_b64:    str
    css_tests:        List[dict] = []
    html_tests:       List[dict] = []
    responsive_tests: List[dict] = []
    watermark_token:  Optional[str] = None

    @field_validator('html_code')
    @classmethod
    def validate_html(cls, v: str) -> str:
        if len(v.encode('utf-8')) > _MAX_HTML_BYTES:
            raise ValueError(f'html_code too large (max {_MAX_HTML_BYTES // 1024}KB)')
        return v

    @field_validator('css_code')
    @classmethod
    def validate_css(cls, v: str) -> str:
        if len(v.encode('utf-8')) > _MAX_CSS_BYTES:
            raise ValueError(f'css_code too large (max {_MAX_CSS_BYTES // 1024}KB)')
        return v

    @field_validator('screenshot_b64', 'reference_b64')
    @classmethod
    def validate_image(cls, v: str) -> str:
        if len(v) > _MAX_IMAGE_BYTES:
            raise ValueError('Image too large (max 5MB base64)')
        return v

    @field_validator('css_tests', 'html_tests', 'responsive_tests')
    @classmethod
    def validate_tests(cls, v: list) -> list:
        if len(v) > _MAX_TESTS:
            raise ValueError(f'Too many test cases (max {_MAX_TESTS})')
        return v


class WatermarkTokenRequest(BaseModel):
    challenge_id: str
    student_id:   str


# ─── Frontend evaluation routes ───────────────────────────────────────────────

@app.post("/api/evaluate/frontend")
async def evaluate_frontend(req: FrontendEvalRequest, _: str = Depends(verify_api_key)):
    """
    Evaluate an HTML/CSS frontend submission.

    Expects:
      - html_code, css_code          — raw source strings
      - screenshot_b64               — base64 PNG of student's rendered output
      - reference_b64                — base64 PNG of the reference output (DB fetches and forwards this)
      - css_tests, html_tests, responsive_tests — test case arrays (DB fetches and forwards these)
      - watermark_token              — optional; if provided, screenshot is verified before scoring

    Returns:
      { score, passed, breakdown: { visual, css, structure, responsive, a11y } }
    """
    import base64

    try:
        student_bytes   = base64.b64decode(req.screenshot_b64)
        reference_bytes = base64.b64decode(req.reference_b64)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_base64"})

    # Watermark check — only if token was supplied
    if req.watermark_token:
        if not verify_watermark(student_bytes, req.watermark_token):
            return JSONResponse(status_code=400, content={"error": "invalid_submission"})

    try:
        visual     = compare_images(reference_bytes, student_bytes)
        css_result = check_css(req.css_code, req.css_tests)
        html_result = check_html_structure(req.html_code, req.html_tests)
        resp_result = check_responsive(req.css_code, req.responsive_tests)
        a11y_result = check_accessibility(req.html_code)
    except Exception as e:
        logger.error(f"Frontend eval error: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "evaluation_failed", "detail": str(e)})

    final_score = round(
        (visual["ssim"]                                             * 100 * 0.35) +
        (css_result["score"]  / max(css_result["max_score"],  1)   * 100 * 0.25) +
        (html_result["score"] / max(html_result["max_score"], 1)   * 100 * 0.15) +
        (resp_result["score"] / max(resp_result["max_score"], 1)   * 100 * 0.10) +
        (a11y_result["score"] / max(a11y_result["max_score"], 1)   * 100 * 0.15)
    )
    passed = final_score >= 70

    return {
        "score":  final_score,
        "passed": passed,
        "breakdown": {
            "visual":     visual,
            "css":        css_result,
            "structure":  html_result,
            "responsive": resp_result,
            "a11y":       a11y_result,
        },
    }


@app.post("/api/challenge/watermark-token")
async def get_watermark_token(_req: WatermarkTokenRequest, _: str = Depends(verify_api_key)):
    """
    Generate a fresh watermark token for a challenge session.
    The DB should store this with a TTL and validate it before calling /api/evaluate/frontend.
    Returns: { "token": "a3f9c2b1" }
    """
    token = generate_token()
    return {"token": token}


@app.post("/auth/generate-key")
async def generate_key(_: str = Depends(verify_api_key)):
    """Generate a new API key (requires existing valid key)."""
    import secrets
    new_key = secrets.token_urlsafe(32)
    logger.info("New API key generated")
    return {
        "api_key": new_key,
        "header": settings.API_KEY_HEADER,
        "note": "Add this key to the ALLOWED_API_KEYS environment variable.",
    }
