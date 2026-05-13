"""Global SystemConfig repository (single-row table)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.employee import SystemConfig


async def get() -> SystemConfig | None:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(SystemConfig).where(SystemConfig.id == 1))).scalar_one_or_none()


async def upsert(default_models: dict | None = None,
                 global_prompts: dict | None = None,
                 system: dict | None = None) -> SystemConfig:
    async with AsyncSessionLocal() as s:
        cfg = (await s.execute(select(SystemConfig).where(SystemConfig.id == 1))).scalar_one_or_none()
        if cfg is None:
            cfg = SystemConfig(
                id=1,
                default_models=default_models or {},
                global_prompts=global_prompts or {},
                system=system or {},
            )
            s.add(cfg)
        else:
            if default_models is not None:
                cfg.default_models = default_models
            if global_prompts is not None:
                cfg.global_prompts = global_prompts
            if system is not None:
                cfg.system = system
            cfg.version = (cfg.version or 1) + 1
        await s.commit()
        await s.refresh(cfg)
        return cfg


async def update_fields(patch: dict[str, Any]) -> SystemConfig | None:
    async with AsyncSessionLocal() as s:
        cfg = (await s.execute(select(SystemConfig).where(SystemConfig.id == 1))).scalar_one_or_none()
        if not cfg:
            return None
        for field, value in patch.items():
            setattr(cfg, field, value)
        cfg.version = (cfg.version or 1) + 1
        await s.commit()
        await s.refresh(cfg)
        return cfg


def to_dict(cfg: SystemConfig) -> dict[str, Any]:
    return {
        "default_models": cfg.default_models,
        "global_prompts": cfg.global_prompts,
        "system": cfg.system,
        "version": cfg.version,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }
