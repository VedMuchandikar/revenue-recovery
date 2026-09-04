"""Main API application for revenue recovery system."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from app.api.endpoints import events, metrics, audit
from app.config.settings import settings
from app.db.database import init_db
from app.batch.runner import batch_runner
from app.webhooks.handlers import router as webhook_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Revenue Recovery AI Agent...")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Start batch processors in background
    try:
        await batch_runner.start_all()
        logger.info("Batch processors started")
    except Exception as e:
        logger.warning(f"Could not start batch processors: {e}")

    yield

    # Shutdown
    logger.info("Shutting down Revenue Recovery AI Agent...")
    try:
        await batch_runner.stop_all()
        logger.info("Batch processors stopped")
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