from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from loguru import logger
import sys
import threading
import time

from app.core.config import settings
from app.api.upload import router as upload_router
from app.api.features import router as features_router
from app.api.auth import router as auth_router
from app.api.tus_upload import router as tus_upload_router

# Configure logging
logger.remove()
logger.add(sys.stdout, level="INFO" if not settings.debug else "DEBUG")

# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    description="AI-powered document processing with Q&A, summary, and quiz generation",
    version="1.0.0",
    debug=settings.debug,
    # Configure for large file uploads
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
import os
from pathlib import Path

# Get absolute path to static directory
current_dir = Path(__file__).parent
static_dir = current_dir / "static"

# Ensure static directory exists
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    print(f"Static files mounted from: {static_dir}")
else:
    print(f"WARNING: Static directory not found: {static_dir}")

# Include routers
app.include_router(auth_router, prefix="/api")
app.include_router(upload_router, prefix="/api")
app.include_router(tus_upload_router, prefix="/api")
app.include_router(features_router, prefix="/api")

@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "Welcome to Study AI Assistant",
        "version": "1.0.0",
        "features": [
            "PDF and Video text extraction",
            "Question & Answer with RAG",
            "Document Summarization",
            "Quiz Generation"
        ],
        "endpoints": {
            "upload": "/api/upload/file",
            "status": "/api/upload/status/{document_id}",
            "documents": "/api/upload/documents",
            "question": "/api/features/question",
            "summary": "/api/features/summary",
            "quiz": "/api/features/quiz",
            "frontend": "/static/index.html"
        }
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "app": settings.app_name}

@app.get("/app")
async def serve_app():
    """Serve the main application"""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    else:
        return {"error": "Frontend not found", "path": str(index_path)}



# Auto-test on startup
# @app.on_event("startup")
# async def startup_event():
#     """Run auto-tests when server starts"""
#     def run_tests():
#         # Wait a bit for server to fully start
#         time.sleep(3)
#         try:
#             from app.utils.startup_test import run_startup_test
#             logger.info("🚀 Starting automatic system tests...")
#             run_startup_test()
#         except Exception as e:
#             logger.error(f"Startup test error: {e}")
    
#     # Run tests in background thread
#     test_thread = threading.Thread(target=run_tests, daemon=True)
#     test_thread.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug
    )