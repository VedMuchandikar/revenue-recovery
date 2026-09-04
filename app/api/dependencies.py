"""API dependencies."""

from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db


# Re-export get_db for convenience
DbSession = Depends(get_db)