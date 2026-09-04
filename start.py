#!/usr/bin/env python3
"""
Start script for Revenue Recovery AI Agent.

This script starts:
1. FastAPI web server (for API + dashboard)
2. Background worker that processes events

Usage:
    python start.py

The agent will automatically process any PENDING events in the database.
"""

import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import after logging is configured
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.endpoints import events, metrics, audit
from app.config.settings import settings
from app.db.database import init_db
from app.batch.runner import batch_runner
from app.webhooks.handlers import router as webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    # Startup
    logger.info("="*60)
    logger.info("Starting Revenue Recovery AI Agent...")
    logger.info("="*60)

    # Initialize database
    await init_db()
    logger.info("✓ Database initialized")

    # Start batch processors in background
    try:
        await batch_runner.start_all()
        logger.info("✓ Background workers started")
    except Exception as e:
        logger.warning(f"⚠ Could not start batch processors: {e}")

    logger.info("="*60)
    logger.info("Agent is running! Processing pending events...")
    logger.info("="*60)

    yield

    # Shutdown
    logger.info("Shutting down...")
    try:
        await batch_runner.stop_all()
    except Exception as e:
        logger.warning(f"Error stopping batch processors: {e}")


# Create FastAPI app
app = FastAPI(
    title="Revenue Recovery AI Agent",
    description="AI-powered revenue recovery system for Razorpay",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(metrics.router, prefix="/api/metrics", tags=["metrics"])
app.include_router(audit.router, prefix="/api/audit", tags=["audit"])
app.include_router(webhook_router)

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "revenue-recovery-agent"}

# Serve Dashboard
@app.get("/")
async def root():
    """Serve the dashboard UI."""
    return FileResponse("/Users/vedmuchandikar/Documents/razorpay/revenue-recovery/dashboard/index.html")


def main():
    """Run the application."""
    import uvicorn

    logger.info("="*60)
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║     REVENUE RECOVERY AI AGENT - STARTING                 ║
    ╠══════════════════════════════════════════════════════════╣
    ║                                                          ║
    ║  Dashboard:    http://localhost:8000                     ║
    ║  API Docs:     http://localhost:8000/docs                ║
    ║  Health:       http://localhost:8000/health              ║
    ║                                                          ║
    ║  The agent will automatically process PENDING events    ║
    ║  in the background.                                      ║
    ║                                                          ║
    ╚══════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "start:app",
        host="127.0.0.1",
        port=8000,
        reload=False,  # Set to True for development
        log_level="info"
    )


if __name__ == "__main__":
    main()