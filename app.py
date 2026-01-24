# app.py - Single Service Architecture (Render Free Tier Compatible)

from fastapi import FastAPI, HTTPException, Request, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator
from typing import List, Dict, Optional
import logging
import asyncio
import uuid
from datetime import datetime
from judge.judge import judge_submission
from auth import verify_api_key, rate_limiter
from config import settings

# Configure logging
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

# CORS middleware (optional)
if settings.ENABLE_CORS:
    origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["POST", "GET"],
        allow_headers=["*"],
    )

# In-memory task storage (survives during runtime)
tasks: Dict[str, Dict] = {}
task_queue = asyncio.Queue(maxsize=100)  # Max 100 queued tasks

class TestCase(BaseModel):
    input: str
    output: str
    
    @validator('input', 'output')
    def validate_length(cls, v):
        if len(v) > 10000:
            raise ValueError('Input/output too large')
        return v

class JudgeRequest(BaseModel):
    language: str
    sourceCode: str
    testcases: List[TestCase]
    
    @validator('language')
    def validate_language(cls, v):
        allowed = ['c', 'cpp', 'python']
        if v.lower() not in allowed:
            raise ValueError(f'Language must be one of: {allowed}')
        return v.lower()
    
    @validator('testcases')
    def validate_testcases(cls, v):
        if len(v) < 1 or len(v) > 10:
            raise ValueError('Must have 1-10 test cases')
        return v
    
    @validator('sourceCode')
    def validate_source(cls, v):
        if len(v.encode('utf-8')) > 100 * 1024:
            raise ValueError('Source code too large')
        dangerous_patterns = ['__import__', 'eval(', 'exec(', 'compile(']
        v_lower = v.lower()
        for pattern in dangerous_patterns:
            if pattern in v_lower:
                raise ValueError(f'Potentially dangerous code pattern detected: {pattern}')
        return v

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"verdict": "Internal Server Error", "error": "An unexpected error occurred"}
    )

async def process_queue():
    """Background worker that processes the task queue"""
    logger.info("Queue processor started")
    
    while True:
        try:
            # Get task from queue (waits if empty)
            task_id, source_code, testcases, language = await task_queue.get()
            
            logger.info(f"Processing task {task_id}")
            
            # Update task status
            tasks[task_id]["status"] = "processing"
            tasks[task_id]["message"] = "Executing submission..."
            
            # Execute the judgment (blocking but in background)
            try:
                result = await asyncio.to_thread(
                    judge_submission,
                    source_code=source_code,
                    testcases=testcases,
                    language=language
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
                    "error": str(e)
                }
            
            # Mark task as done
            task_queue.task_done()
            
        except Exception as e:
            logger.error(f"Queue processor error: {e}", exc_info=True)
            await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    """Start background queue processor on startup"""
    # Start multiple workers for concurrency
    num_workers = settings.MAX_CONCURRENT_TASKS
    for i in range(num_workers):
        asyncio.create_task(process_queue())
    logger.info(f"Started {num_workers} background workers")

@app.post("/judge")
async def judge(req: JudgeRequest, api_key: str = Depends(verify_api_key)):
    """
    Submit code for asynchronous judgment
    Returns task_id immediately, code executes in background
    """
    try:
        # Check queue capacity
        if task_queue.qsize() >= settings.MAX_QUEUE_SIZE:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "error": "Queue is full. Please try again later."
                }
            )
        
        # Generate task ID
        task_id = str(uuid.uuid4())
        
        # Create task record
        tasks[task_id] = {
            "status": "queued",
            "message": "Submission queued for execution",
            "created_at": datetime.utcnow().isoformat(),
            "language": req.language,
            "result": None
        }
        
        # Add to queue (non-blocking)
        await task_queue.put((
            task_id,
            req.sourceCode,
            [tc.dict() for tc in req.testcases],
            req.language
        ))
        
        logger.info(f"Task {task_id} queued (queue size: {task_queue.qsize()})")
        
        # Return immediately
        remaining = rate_limiter.get_remaining(api_key)
        return JSONResponse(
            content={
                "status": "queued",
                "task_id": task_id,
                "message": "Submission queued for execution. Use /status/{task_id} to check progress.",
                "queue_position": task_queue.qsize()
            },
            headers={
                "X-RateLimit-Limit": str(settings.RATE_LIMIT_REQUESTS),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset": str(settings.RATE_LIMIT_WINDOW)
            }
        )
        
    except Exception as e:
        logger.error(f"Judge error: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}

@app.get("/status/{task_id}")
async def get_status(task_id: str, api_key: str = Depends(verify_api_key)):
    """
    Get the status of a submission task
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    
    response = {
        "status": task["status"],
        "message": task.get("message", ""),
        "created_at": task.get("created_at")
    }
    
    if task["status"] == "completed":
        response["result"] = task["result"]
        response["completed_at"] = task.get("completed_at")
    elif task["status"] == "failed":
        response["result"] = task["result"]
    
    return response

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "queue_size": task_queue.qsize(),
        "active_tasks": len([t for t in tasks.values() if t["status"] == "processing"]),
        "total_tasks": len(tasks),
        "workers": settings.MAX_CONCURRENT_TASKS
    }

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": settings.SERVICE_NAME,
        "version": settings.VERSION,
        "supported_languages": ["c", "cpp", "python"],
        "status": "online",
        "queue_size": task_queue.qsize()
    }

@app.post("/auth/generate-key")
async def generate_key(master_key: str = Depends(verify_api_key)):
    """Generate a new API key (Protected)"""
    import secrets
    new_key = secrets.token_urlsafe(32)
    logger.info(f"New API key generated by {master_key[:8]}...")
    return {
        "api_key": new_key,
        "header": settings.API_KEY_HEADER,
        "note": "Add this to ALLOWED_API_KEYS environment variable"
    }

# Cleanup old completed tasks periodically
@app.on_event("startup")
async def start_cleanup():
    async def cleanup_old_tasks():
        while True:
            await asyncio.sleep(3600)  # Every hour
            now = datetime.utcnow()
            to_delete = []
            
            for task_id, task in tasks.items():
                if task["status"] in ["completed", "failed"]:
                    created = datetime.fromisoformat(task["created_at"])
                    if (now - created).total_seconds() > 3600:  # Older than 1 hour
                        to_delete.append(task_id)
            
            for task_id in to_delete:
                del tasks[task_id]
            
            if to_delete:
                logger.info(f"Cleaned up {len(to_delete)} old tasks")
    
    asyncio.create_task(cleanup_old_tasks())