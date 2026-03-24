# SPDX-License-Identifier: LGPL-3.0-only
# Copyright (c) 2026 Mirrowel

import logging
import os
import time
from typing import Optional

lib_logger = logging.getLogger("rotator_library")

_redis_backend: Optional["RedisBackend"] = None


def get_redis_backend() -> Optional["RedisBackend"]:
    return _redis_backend


class RedisBackend:
    """
    Thin async wrapper around redis.asyncio.
    Provides atomic operations needed for distributed state:
    - Concurrent slot counting (INCR/DECR with ceiling check)
    - Cooldown TTL keys (SET EX / EXISTS)
    - Provider-level cooldown
    """

    def __init__(self, redis_url: str, prefix: str = "llmproxy:"):
        self._url = redis_url
        self._prefix = prefix
        self._client = None

    async def connect(self) -> bool:
        try:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            await self._client.ping()
            lib_logger.info(f"Redis connected: {self._url} (prefix={self._prefix})")
            return True
        except Exception as e:
            lib_logger.warning(f"Redis connection failed: {e}. Falling back to local mode.")
            self._client = None
            return False

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def _k(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    async def concurrent_increment(self, key: str, model: str, max_val: int) -> bool:
        """
        Atomically increment concurrent count for key+model if below max_val.
        Returns True if acquired, False if at capacity.
        """
        redis_key = self._k("concurrent", key, model)
        lua = """
        local current = tonumber(redis.call('GET', KEYS[1]) or '0')
        if current < tonumber(ARGV[1]) then
            redis.call('SET', KEYS[1], current + 1)
            return 1
        end
        return 0
        """
        result = await self._client.eval(lua, 1, redis_key, str(max_val))
        return bool(result)

    async def concurrent_decrement(self, key: str, model: str) -> int:
        """
        Decrement concurrent count. Returns remaining count (>=0).
        """
        redis_key = self._k("concurrent", key, model)
        lua = """
        local current = tonumber(redis.call('GET', KEYS[1]) or '0')
        if current <= 1 then
            redis.call('DEL', KEYS[1])
            return 0
        else
            redis.call('SET', KEYS[1], current - 1)
            return current - 1
        end
        """
        result = await self._client.eval(lua, 1, redis_key)
        return int(result)

    async def concurrent_get(self, key: str, model: str) -> int:
        redis_key = self._k("concurrent", key, model)
        val = await self._client.get(redis_key)
        return int(val) if val else 0

    async def concurrent_get_all_models(self, key: str) -> dict:
        """
        Returns {model: count} for all models currently in use by this key.
        """
        pattern = self._k("concurrent", key, "*")
        prefix_len = len(self._k("concurrent", key, ""))
        result = {}
        async for redis_key in self._client.scan_iter(pattern):
            model_name = redis_key[prefix_len:]
            val = await self._client.get(redis_key)
            if val and int(val) > 0:
                result[model_name] = int(val)
        return result

    async def set_cooldown(self, ns: str, identifier: str, seconds: float):
        """
        Set a cooldown that expires after `seconds`.
        ns: namespace, e.g. 'key', 'model', 'provider'
        identifier: unique id within namespace
        """
        redis_key = self._k("cooldown", ns, identifier)
        expire_at = time.time() + seconds
        await self._client.set(redis_key, str(expire_at), ex=max(1, int(seconds) + 1))

    async def get_cooldown_remaining(self, ns: str, identifier: str) -> float:
        """
        Returns seconds remaining in cooldown, 0 if not cooling down.
        """
        redis_key = self._k("cooldown", ns, identifier)
        val = await self._client.get(redis_key)
        if not val:
            return 0.0
        expire_at = float(val)
        remaining = expire_at - time.time()
        return max(0.0, remaining)

    async def is_cooling_down(self, ns: str, identifier: str) -> bool:
        remaining = await self.get_cooldown_remaining(ns, identifier)
        return remaining > 0

    async def clear_cooldown(self, ns: str, identifier: str):
        redis_key = self._k("cooldown", ns, identifier)
        await self._client.delete(redis_key)

    async def usage_increment(self, key: str, field: str, amount: int = 1):
        redis_key = self._k("usage", key)
        await self._client.hincrbyfloat(redis_key, field, amount)

    async def usage_get(self, key: str) -> dict:
        redis_key = self._k("usage", key)
        return await self._client.hgetall(redis_key)


async def init_redis_backend() -> Optional[RedisBackend]:
    global _redis_backend
    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        lib_logger.info("REDIS_URL not set. Running in single-node mode.")
        return None
    prefix = os.getenv("REDIS_KEY_PREFIX", "llmproxy")
    backend = RedisBackend(redis_url=redis_url, prefix=prefix)
    ok = await backend.connect()
    if ok:
        _redis_backend = backend
        return backend
    return None
