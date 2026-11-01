from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class QueueMessage:
    job_id: UUID
    tenant_id: UUID
    kind: str


class QueueBroker(Protocol):
    async def enqueue(self, message: QueueMessage) -> None: ...

    async def dequeue(self, timeout_seconds: int = 1) -> QueueMessage | None: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...
