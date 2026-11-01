from __future__ import annotations

import asyncio
import json
from uuid import UUID

from redis.asyncio import Redis

from clipos.queue.base import QueueMessage


class InMemoryQueueBroker:
    """Development/test broker. It is intentionally unavailable as a production choice."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueueMessage] = asyncio.Queue()

    async def enqueue(self, message: QueueMessage) -> None:
        await self._queue.put(message)

    async def dequeue(self, timeout_seconds: int = 1) -> QueueMessage | None:
        if timeout_seconds <= 0:
            try:
                return self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout_seconds)
        except TimeoutError:
            return None

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class RedisQueueBroker:
    QUEUE_KEY = "clipos:jobs:v1"

    def __init__(self, url: str) -> None:
        self._redis = Redis.from_url(url, decode_responses=True)

    async def enqueue(self, message: QueueMessage) -> None:
        payload = json.dumps(
            {
                "job_id": str(message.job_id),
                "tenant_id": str(message.tenant_id),
                "kind": message.kind,
            },
            separators=(",", ":"),
        )
        await self._redis.rpush(self.QUEUE_KEY, payload)

    async def dequeue(self, timeout_seconds: int = 1) -> QueueMessage | None:
        if timeout_seconds <= 0:
            payload = await self._redis.lpop(self.QUEUE_KEY)
            if payload is None:
                return None
        else:
            result = await self._redis.blpop(self.QUEUE_KEY, timeout=timeout_seconds)
            if result is None:
                return None
            _, payload = result
        data = json.loads(payload)
        return QueueMessage(
            job_id=UUID(data["job_id"]),
            tenant_id=UUID(data["tenant_id"]),
            kind=data["kind"],
        )

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def close(self) -> None:
        await self._redis.aclose()
