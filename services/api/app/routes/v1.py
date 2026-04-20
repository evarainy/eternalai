"""Versioned public API route group for future business endpoints."""

from fastapi import APIRouter


def build_v1_router() -> APIRouter:
    return APIRouter(prefix="/v1")
