# config.py

from pydantic_settings import BaseSettings
from typing import Optional
import secrets

class Settings(BaseSettings):
    """
    Application settings with environment variable support
    """
    # API Authentication
    API_KEY: str = secrets.token_urlsafe(32)  # Generate random key if not set
    API_KEY_HEADER: str = "X-API-Key"
    
    # Optional: Multiple API keys for different services
    ALLOWED_API_KEYS: Optional[str] = None  # Comma-separated list
    
    # Service settings
    SERVICE_NAME: str = "Lumetrix Judge Service"
    VERSION: str = "1.0.0"
    
    # Security
    ENABLE_CORS: bool = False
    ALLOWED_ORIGINS: str = "http://localhost:3000"  # Comma-separated
    
    # Rate limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 100  # requests per window
    RATE_LIMIT_WINDOW: int = 60     # seconds
    
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

# Global settings instance
settings = Settings()