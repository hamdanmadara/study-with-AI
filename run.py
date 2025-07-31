#!/usr/bin/env python3
"""
Simple script to run the FastAPI application
"""
import uvicorn

if __name__ == "__main__":
    print("Starting Study AI Assistant...")
    print("")
    print("FEATURES:")
    print("  PDF processing: ENABLED")
    print("  Video processing: ENABLED (with speech-to-text)")
    print("  AI Embeddings: Auto-downloading best model (all-MiniLM-L6-v2)")
    print("  Auto-testing: Will test with your files automatically")
    print("")
    print("ACCESS:")
    print("  Web Interface: http://localhost:8000/app")
    print("  API Documentation: http://localhost:8000/docs")
    print("")
    print("WHAT HAPPENS NEXT:")
    print("  1. Server starts instantly")
    print("  2. Embedding model downloads automatically (first time only)")
    print("  3. System tests your PDF file automatically")
    print("  4. Upload and use - everything works seamlessly!")
    print("")
    print("=" * 60)
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )