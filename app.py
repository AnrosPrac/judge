# app.py - Single Service Architecture (Render Free Tier Compatible)

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator
from typing import List, Dict, Optional
import logging
import asyncio
import uuid
from datetime import datetime
from judge.judge import judge_submission, run_submission
from auth import verify_api_key, rate_limiter
from config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.SERVICE_NAME,
    version=settings.VERSION,
    docs_url="/docs" if settings.LOG_LEVEL == "DEBUG" else None,
    redoc_url="/redoc" if settings.LOG_LEVEL == "DEBUG" else None
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

# In-memory task storage
tasks: Dict[str, Dict] = {}

# Queue reads capacity from config — not hardcoded
task_queue: asyncio.Queue = None  # initialised in startup

# Semaphore that caps concurrent /run executions independently of /judge workers
run_semaphore: asyncio.Semaphore = None  # initialised in startup


# ─── Request models ───────────────────────────────────────────────────────────

class TestCase(BaseModel):
    input: str
    output: str

    @validator('input', 'output')
    def validate_length(cls, v):
        if len(v) > 10000:
            raise ValueError('Input/output too large')
        return v


def _validate_language(v: str) -> str:
    allowed = ['c', 'cpp', 'python', 'java']
    if v.lower() not in allowed:
        raise ValueError(f'Language must be one of: {allowed}')
    return v.lower()


def _validate_source(v: str) -> str:
    if len(v.encode('utf-8')) > 100 * 1024:
        raise ValueError('Source code too large')
    return v


class JudgeRequest(BaseModel):
    language: str
    sourceCode: str
    testcases: List[TestCase]

    @validator('language')
    def validate_language(cls, v):
        return _validate_language(v)

    @validator('testcases')
    def validate_testcases(cls, v):
        if len(v) < 1 or len(v) > 20:
            raise ValueError('Must have 1-20 test cases')
        return v

    @validator('sourceCode')
    def validate_source(cls, v):
        return _validate_source(v)


class RunRequest(BaseModel):
    language: str
    sourceCode: str
    stdin: Optional[str] = ""

    @validator('language')
    def validate_language(cls, v):
        return _validate_language(v)

    @validator('sourceCode')
    def validate_source(cls, v):
        return _validate_source(v)

    @validator('stdin')
    def validate_stdin(cls, v):
        if v and len(v.encode('utf-8')) > 10 * 1024:
            raise ValueError('stdin too large (max 10KB)')
        return v or ""


# ─── Global exception handler ─────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"verdict": "Internal Server Error", "error": "An unexpected error occurred"}
    )


# ─── Judge queue worker ───────────────────────────────────────────────────────

async def process_queue():
    """Background worker — one coroutine per MAX_CONCURRENT_TASKS."""
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
                tasks[task_id]["completed_at"] = datetime.utcnow().isoformat()
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


# ─── Startup / shutdown ───────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    global task_queue, run_semaphore

    # Queue size comes from config — no more hardcoded 100
    task_queue = asyncio.Queue(maxsize=settings.MAX_QUEUE_SIZE)

    # /run gets its own concurrency cap, completely separate from judge workers
    run_semaphore = asyncio.Semaphore(settings.MAX_RUN_CONCURRENT)

    # Spin up judge workers
    for _ in range(settings.MAX_CONCURRENT_TASKS):
        asyncio.create_task(process_queue())

    logger.info(
        f"Started {settings.MAX_CONCURRENT_TASKS} judge workers, "
        f"{settings.MAX_RUN_CONCURRENT} run slots, "
        f"queue capacity {settings.MAX_QUEUE_SIZE}"
    )

    asyncio.create_task(_cleanup_loop())


async def _cleanup_loop():
    """Delete completed/failed tasks older than 1 hour."""
    while True:
        await asyncio.sleep(3600)
        now = datetime.utcnow()
        to_delete = [
            tid for tid, t in tasks.items()
            if t["status"] in ("completed", "failed")
            and (now - datetime.fromisoformat(t["created_at"])).total_seconds() > 3600
        ]
        for tid in to_delete:
            del tasks[tid]
        if to_delete:
            logger.info(f"Cleaned up {len(to_delete)} old tasks")


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
            "created_at": datetime.utcnow().isoformat(),
            "language": req.language,
            "result": None,
        }

        await task_queue.put((
            task_id,
            req.sourceCode,
            [tc.dict() for tc in req.testcases],
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
async def get_status(task_id: str, api_key: str = Depends(verify_api_key)):
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
async def run(req: RunRequest, api_key: str = Depends(verify_api_key)):
    """
    Compile and run code with optional stdin. Returns output immediately.
    Uses a separate concurrency pool — never blocks /judge workers.
    """
    async with run_semaphore:
        try:
            result = await asyncio.to_thread(
                run_submission,
                source_code=req.sourceCode,
                language=req.language,
                stdin=req.stdin,
            )
            return JSONResponse(content=result)
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
        "supported_languages": ["c", "cpp", "python", "java"],
        "status": "online",
        "queue_size": task_queue.qsize() if task_queue else 0,
    }


@app.post("/auth/generate-key")
async def generate_key(master_key: str = Depends(verify_api_key)):
    """Generate a new API key (requires existing valid key)."""
    import secrets
    new_key = secrets.token_urlsafe(32)
    logger.info(f"New API key generated by {master_key[:8]}...")
    return {
        "api_key": new_key,
        "header": settings.API_KEY_HEADER,
        "note": "Add this key to the ALLOWED_API_KEYS environment variable.",
    }
