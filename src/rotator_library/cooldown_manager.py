# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2026 Mirrowel

import asyncio
import time
from typing import Dict, Optional


class CooldownManager:
    """
    Manages global cooldown periods for API providers to handle IP-based rate limiting.
    Supports both local (single-node) and Redis (distributed) backends.
    When a RedisBackend is provided, cooldown state is shared across all nodes.
    """

    def __init__(self, redis_backend=None):
        self._cooldowns: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._redis = redis_backend

    def set_redis_backend(self, redis_backend):
        self._redis = redis_backend

    async def is_cooling_down(self, provider: str) -> bool:
        if self._redis and self._redis.available:
            try:
                return await self._redis.is_cooling_down("provider", provider)
            except Exception:
                pass
        async with self._lock:
            return provider in self._cooldowns and time.time() < self._cooldowns[provider]

    async def start_cooldown(self, provider: str, duration: int):
        if self._redis and self._redis.available:
            try:
                await self._redis.set_cooldown("provider", provider, duration)
            except Exception:
                pass
        async with self._lock:
            self._cooldowns[provider] = time.time() + duration

    async def get_cooldown_remaining(self, provider: str) -> float:
        if self._redis and self._redis.available:
            try:
                return await self._redis.get_cooldown_remaining("provider", provider)
            except Exception:
                pass
        async with self._lock:
            if provider in self._cooldowns:
                remaining = self._cooldowns[provider] - time.time()
                return max(0, remaining)
            return 0
