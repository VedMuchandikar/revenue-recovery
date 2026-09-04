#!/usr/bin/env python3
"""Initialize database and run migrations."""

import asyncio
from sqlalchemy import text


async def init_database():
    """Initialize the database."""
    from app.db.database import init_db, async_session_factory
    from app.db.models import Base

    print("Creating database tables...")

    # Import all models to register them
    from app.db import models  # noqa: F401

    await init_db()
    print("Database tables created successfully!")

    # Verify tables using a connection
    async with async_session_factory() as session:
        # Get table names via raw SQL
        result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table';"))
        tables = [row[0] for row in result.fetchall()]
        print(f"\nCreated tables: {tables}")

        # Count tables
        print(f"Total tables: {len(tables)}")

        # Show schema for revenue_events
        print("\nRevenueEvents table schema:")
        result = await session.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='revenue_events';"))
        schema = result.fetchone()
        if schema:
            print(schema[0])


if __name__ == "__main__":
    asyncio.run(init_database())