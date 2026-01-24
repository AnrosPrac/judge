# config_simple.py - Configuration without Redis

from pydantic_settings import BaseSettings
from typing import Optional
import secrets

class Settings(BaseSettings):
    """
    Application settings - Single service mode
    """
    # API Authentication
    API_KEY: str = secrets.token_urlsafe(32)
    API_KEY_HEADER: str = "X-API-Key"
    ALLOWED_API_KEYS: Optional[str] = None
    
    # Service settings
    SERVICE_NAME: str = "Lumetrix Judge Service"
    VERSION: str = "2.0.0-single"
    
    # Worker settings (in-process)
    MAX_CONCURRENT_TASKS: int = 5  # Process 5 submissions at once
    MAX_QUEUE_SIZE: int = 50  # Max 50 submissions in queue
    
    # Security
    ENABLE_CORS: bool = False
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    
    # Rate limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    class Config:
        env_file = ".env"
        case_sensitive = True
    
    def get_allowed_keys(self) -> set:
        """Get set of all allowed API keys"""
        keys = {self.API_KEY}
        if self.ALLOWED_API_KEYS:
            keys.update(k.strip() for k in self.ALLOWED_API_KEYS.split(","))
        return keys

settings = Settings()