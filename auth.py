# judge/auth.py

from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from config import settings
import logging
import time
from collections import defaultdict
from typing import Dict

logger = logging.getLogger(__name__)

# API Key security scheme
api_key_header = APIKeyHeader(name=settings.API_KEY_HEADER, auto_error=False)

# Simple in-memory rate limiter (use Redis in production)
class RateLimiter:
    def __init__(self):
        self.requests: Dict[str, list] = defaultdict(list)
    
    def is_allowed(self, api_key: str) -> bool:
        """Check if request is within rate limit"""
        if not settings.RATE_LIMIT_ENABLED:
            return True
        
        now = time.time()
        window_start = now - settings.RATE_LIMIT_WINDOW
        
        # Clean old requests
        self.requests[api_key] = [
            req_time for req_time in self.requests[api_key]
            if req_time > window_start
        ]
        
        # Check limit
        if len(self.requests[api_key]) >= settings.RATE_LIMIT_REQUESTS:
            return False
        
        # Add current request
        self.requests[api_key].append(now)
        return True
    
    def get_remaining(self, api_key: str) -> int:
        """Get remaining requests in current window"""
        return max(0, settings.RATE_LIMIT_REQUESTS - len(self.requests[api_key]))

rate_limiter = RateLimiter()

async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """
    Verify API key and enforce rate limiting
    """
    # Check if API key is provided
    if not api_key:
        logger.warning("Missing API key in request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
    # Verify API key
    allowed_keys = settings.get_allowed_keys()
    if api_key not in allowed_keys:
        logger.warning(f"Invalid API key attempted: {api_key[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key"
        )
    
    # Check rate limit
    if not rate_limiter.is_allowed(api_key):
        logger.warning(f"Rate limit exceeded for API key: {api_key[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
            headers={
                "Retry-After": str(settings.RATE_LIMIT_WINDOW),
                "X-RateLimit-Limit": str(settings.RATE_LIMIT_REQUESTS),
                "X-RateLimit-Remaining": "0"
            }
        )
    
    # Add rate limit headers info
    remaining = rate_limiter.get_remaining(api_key)
    logger.debug(f"API key validated: {api_key[:8]}... (remaining: {remaining})")
    
    return api_key