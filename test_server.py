#!/usr/bin/env python3
"""Test server startup and endpoints."""

import asyncio
import uvicorn
from start import app
import httpx


async def test_server():
    config = uvicorn.Config(app, host='127.0.0.1', port=8000, log_level='info')
    server = uvicorn.Server(config)

    # Start server in background
    serve_task = asyncio.create_task(server.serve())

    # Give server time to start
    await asyncio.sleep(2)

    try:
        async with httpx.AsyncClient() as client:
            # Test health endpoint
            resp = await client.get("http://localhost:8000/health")
            print(f"Health: {resp.status_code} - {resp.json()}")

            # Test metrics
            resp = await client.get("http://localhost:8000/api/metrics/summary")
            print(f"Metrics: {resp.status_code} - {resp.json()}")

            # Test dashboard
            resp = await client.get("http://localhost:8000/")
            print(f"Dashboard: {resp.status_code} - {len(resp.text)} chars")

    finally:
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(test_server())