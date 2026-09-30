"""FastAPI route dependencies."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from src.infra.config import settings


async def verify_cron_secret(authorization: str = Header(...)) -> None:
    """FastAPI dependency that validates the Bearer token for internal endpoints."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.cron_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing cron secret.",
        )
