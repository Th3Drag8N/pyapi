"""
Entry point — loads .env then starts uvicorn.
Run with:  python run.py
Or with uvicorn directly:  uvicorn app.main:app --reload --port 3030
"""

import os

from dotenv import load_dotenv

load_dotenv()

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "3030"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
