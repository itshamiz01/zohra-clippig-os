from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    key: str
    size: int
    checksum_sha256: str
    content_type: str | None


class ObjectStorage(Protocol):
    async def put(
        self, tenant_id: UUID, key: str, data: bytes, *, content_type: str | None = None
    ) -> ObjectInfo: ...

    async def put_file(
        self,
        tenant_id: UUID,
        key: str,
        source: Path,
        *,
        content_type: str | None = None,
        expected_checksum: str | None = None,
    ) -> ObjectInfo: ...

    async def copy_to(self, tenant_id: UUID, key: str, destination: Path) -> ObjectInfo: ...

    async def get(self, tenant_id: UUID, key: str) -> bytes: ...

    async def path_for_read(self, tenant_id: UUID, key: str) -> Path: ...

    async def head(self, tenant_id: UUID, key: str) -> ObjectInfo | None: ...

    async def delete(self, tenant_id: UUID, key: str) -> bool: ...

    async def list(self, tenant_id: UUID, prefix: str = "") -> list[ObjectInfo]: ...
