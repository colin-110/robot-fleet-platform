from fastapi import Header, HTTPException
from app.config import get_settings

settings = get_settings()

async def verify_api_key(x_api_key: str = Header(None)):
    """Validate the API key header for protected endpoints."""
    if x_api_key != settings.telemetry_api_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")
