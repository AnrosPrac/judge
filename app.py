# app.py

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, validator
from typing import List
import logging
from judge.judge import judge_submission

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Lumetrix Judge Service",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

class TestCase(BaseModel):
    input: str
    output: str
    
    @validator('input', 'output')
    def validate_length(cls, v):
        if len(v) > 10000:  # 10KB max per field
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
        if len(v.encode('utf-8')) > 100 * 1024:  # 100KB
            raise ValueError('Source code too large')
        # Basic security checks
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

@app.post("/judge")
async def judge(req: JudgeRequest):
    """
    Judge a code submission against test cases
    """
    try:
        logger.info(f"Judge request: language={req.language}, testcases={len(req.testcases)}")
        
        result = judge_submission(
            source_code=req.sourceCode,
            testcases=[tc.dict() for tc in req.testcases],
            language=req.language
        )
        
        logger.info(f"Judge result: {result.get('verdict')}")
        return result
        
    except Exception as e:
        logger.error(f"Judge error: {e}", exc_info=True)
        return {"verdict": "System Error", "error": str(e)}

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Lumetrix Judge Service",
        "version": "1.0.0",
        "supported_languages": ["c", "cpp", "python"]
    }