#!/usr/bin/env python3
"""Test script to verify Phase 11: API endpoints implementation."""

import asyncio
import httpx

async def test_api_endpoints():
    print("Testing API endpoints...")
    
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        # Test metrics overview
        response = await client.get("/api/metrics/overview")
        assert response.status_code == 200
        data = response.json()
        print(f"Metrics overview: {data}")
        assert "total_events" in data
        assert "total_at_risk" in data
        
        # Test events listing
        response = await client.get("/api/events")
        assert response.status_code == 200
        events = response.json()
        print(f"Found {len(events)} events")
        assert isinstance(events, list)
        
        # Test specific event if available
        if events:
            event_id = events[0]["id"]
            response = await client.get(f"/api/events/{event_id}")
            assert response.status_code == 200
            event = response.json()
            print(f"Event details for {event_id}: {event['type']} - {event['status']}")
            assert event["id"] == event_id
            
        print("API endpoints working fine!")

if __name__ == "__main__":
    asyncio.run(test_api_endpoints())
