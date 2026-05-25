import os
from fastapi import Header, HTTPException


def verify_api_key(x_api_key: str = Header(...)):
    expected = os.getenv("API_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="API_KEY not configured")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
