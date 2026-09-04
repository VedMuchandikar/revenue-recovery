#!/usr/bin/env python3
"""Demo script to start server, test endpoints, and show it works."""

import asyncio
import sys
import signal

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# Import the app components from start_alt to avoid duplication
from start_alt import app as fastapi_app

async def test_endpoints():
    """Test the server endpoints."""
    import httpx
    await asyncio.sleep(2)  # let server start

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get("http://localhost:8002/health", timeout=5.0)
            print(f"✓ Health: {resp.status_code} - {resp.json()}")
        except Exception as e:
            print(f"✗ Health check failed: {e}")

        try:
            resp = await client.get("http://localhost:8002/api/metrics/summary", timeout=5.0)
            print(f"✓ Metrics: {resp.status_code} - {resp.json()}")
        except Exception as e:
                print(f"✗ Metrics failed: {e}")

        try:
            resp = await client.get("http://localhost:8002/", timeout=5.0)
            print(f"✓ Dashboard: {resp.status_code} - {len(resp.text)} chars")
        except Exception as e:
            print(f"✗ Dashboard failed: {e}")

async def main():
    """Start server and run tests."""
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=8002, log_level="info")
    server = uvicorn.Server(config)

    # Start server in background
    server_task = asyncio.create_task(server.serve())

    try:
        # Run tests
        await test_endpoints()

        print("\nServer is running on http://localhost:8002")
        print("Press Ctrl+C to stop...")

        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.should_exit = True
        await server_task

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDemo stopped.")