"""FastAPI Main Application for YouTube AI Comment Agent."""

import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables from .env file
load_dotenv()

from app.routes.auth import router as auth_router
from app.routes.comment import router as comment_router
from app.routes.channel import router as channel_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup validation checks (does not crash if credentials are not yet populated)
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    ai_provider = os.getenv("AI_PROVIDER", "gemini").strip().lower()

    if not client_id or not client_secret:
        print("[WARNING] Google OAuth credentials (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET) are not set in .env.")
    else:
        print("[INFO] Google OAuth credentials configured.")

    if ai_provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
        print("[WARNING] GEMINI_API_KEY is not set in .env. AI comment generation via Gemini will not work.")
    elif ai_provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        print("[WARNING] OPENAI_API_KEY is not set in .env. AI comment generation via OpenAI will not work.")
    elif ai_provider == "ollama":
        print(f"[INFO] Using Ollama at {os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')}")

    yield


app_name = os.getenv("APP_NAME", "YouTube AI Comment Agent")
debug = os.getenv("DEBUG", "False").lower() in ("true", "1")

app = FastAPI(
    title=app_name,
    description="Automated YouTube Commenting Agent powered by FastAPI, Streamlit, and YouTube Data API v3",
    version="1.0.0",
    debug=debug,
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(auth_router)
app.include_router(comment_router)
app.include_router(channel_router)


@app.get("/health", tags=["Health"])
def health_check():
    """Health check endpoint to verify backend status."""
    return {
        "status": "ok",
        "app_name": os.getenv("APP_NAME", "YouTube AI Comment Agent"),
        "version": "1.0.0",
    }
