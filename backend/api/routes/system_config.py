"""Global system_config CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from backend.repos import config_repo
from backend.services import registry

router = APIRouter(prefix="/api/system-config", tags=["system-config"])


@router.get("")
async def get_system_config() -> dict:
    cfg = await config_repo.get()
    if not cfg:
        raise HTTPException(404, "system_config not initialized")
    return config_repo.to_dict(cfg)


@router.patch("")
async def update_system_config(patch: dict = Body(...)) -> dict:
    allowed = {"default_models", "global_prompts", "system"}
    illegal = set(patch) - allowed
    if illegal:
        raise HTTPException(400, f"unsupported fields: {sorted(illegal)}")
    return await registry.update_global(patch, actor="api")
