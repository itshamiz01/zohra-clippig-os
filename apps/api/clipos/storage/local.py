from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from uuid import UUID

from clipos.storage.base import ObjectInfo

_COPY_CHUNK_BYTES = 1024 * 1024


class UnsafeStorageKey(ValueError):
    pass


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_COPY_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _read_metadata(path: Path, key: str) -> ObjectInfo:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        size = int(data["size"])
        checksum = str(data["checksum_sha256"])
        content_type = data.get("content_type")
        if (
            size < 0
            or len(checksum) != 64
            or any(char not in "0123456789abcdef" for char in checksum)
        ):
            raise ValueError
        if content_type is not None and not isinstance(content_type, str):
            raise TypeError
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise OSError("stored object metadata failed validation") from exc
    return ObjectInfo(
        key=key,
        size=size,
        checksum_sha256=checksum,
        content_type=content_type,
    )


class LocalObjectStorage:
    """Tenant-scoped, non-public development storage with atomic writes."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _validate_key(key: str) -> PurePosixPath:
        if not key or "\x00" in key or "\\" in key:
            raise UnsafeStorageKey("storage key is invalid")
        path = PurePosixPath(key)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise UnsafeStorageKey("storage key must be a normalized relative path")
        return path

    def _tenant_root(self, tenant_id: UUID) -> Path:
        tenant_root = (self.root / str(tenant_id)).resolve()
        if self.root != tenant_root and self.root not in tenant_root.parents:
            raise UnsafeStorageKey("tenant storage root escaped configured storage")
        return tenant_root

    def _path(self, tenant_id: UUID, key: str) -> Path:
        relative = self._validate_key(key)
        tenant_root = self._tenant_root(tenant_id)
        target = (tenant_root / Path(*relative.parts)).resolve()
        if tenant_root != target and tenant_root not in target.parents:
            raise UnsafeStorageKey("storage key escaped tenant root")
        return target

    @staticmethod
    def _metadata_path(target: Path) -> Path:
        return target.with_name(f".{target.name}.meta.json")

    @staticmethod
    def _write_metadata(target: Path, info: ObjectInfo) -> None:
        metadata = {
            "size": info.size,
            "checksum_sha256": info.checksum_sha256,
            "content_type": info.content_type,
        }
        metadata_path = LocalObjectStorage._metadata_path(target)
        fd, temporary_name = tempfile.mkstemp(prefix=".meta-", dir=target.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(metadata, separators=(",", ":")))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, metadata_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    async def put(
        self, tenant_id: UUID, key: str, data: bytes, *, content_type: str | None = None
    ) -> ObjectInfo:
        target = self._path(tenant_id, key)
        checksum = hashlib.sha256(data).hexdigest()
        info = ObjectInfo(
            key=key, size=len(data), checksum_sha256=checksum, content_type=content_type
        )

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, target)
                self._write_metadata(target, info)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)

        await asyncio.to_thread(write)
        return info

    async def put_file(
        self,
        tenant_id: UUID,
        key: str,
        source: Path,
        *,
        content_type: str | None = None,
        expected_checksum: str | None = None,
    ) -> ObjectInfo:
        target = self._path(tenant_id, key)

        def copy() -> ObjectInfo:
            if not source.is_file():
                raise FileNotFoundError("source file does not exist")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
            digest = hashlib.sha256()
            size = 0
            try:
                os.fchmod(fd, 0o600)
                with source.open("rb") as incoming, os.fdopen(fd, "wb") as outgoing:
                    while chunk := incoming.read(_COPY_CHUNK_BYTES):
                        outgoing.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                checksum = digest.hexdigest()
                if expected_checksum and checksum != expected_checksum:
                    raise ValueError("source checksum changed before storage")
                info = ObjectInfo(
                    key=key,
                    size=size,
                    checksum_sha256=checksum,
                    content_type=content_type,
                )
                os.replace(temporary_name, target)
                self._write_metadata(target, info)
                return info
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)

        return await asyncio.to_thread(copy)

    async def copy_to(self, tenant_id: UUID, key: str, destination: Path) -> ObjectInfo:
        target = self._path(tenant_id, key)
        info = await self.head(tenant_id, key)
        if info is None:
            raise FileNotFoundError("stored object does not exist")

        def copy() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary_name = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
            try:
                os.fchmod(fd, 0o600)
                with target.open("rb") as incoming, os.fdopen(fd, "wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=_COPY_CHUNK_BYTES)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                os.replace(temporary_name, destination)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)

        await asyncio.to_thread(copy)
        copied_size, copied_checksum = await asyncio.to_thread(_hash_file, destination)
        if copied_size != info.size or copied_checksum != info.checksum_sha256:
            await asyncio.to_thread(destination.unlink, missing_ok=True)
            raise OSError("stored object failed checksum verification during materialization")
        return info

    async def get(self, tenant_id: UUID, key: str) -> bytes:
        target = self._path(tenant_id, key)
        info = await self.head(tenant_id, key)
        if info is None:
            raise FileNotFoundError("stored object does not exist")
        data = await asyncio.to_thread(target.read_bytes)
        if len(data) != info.size or hashlib.sha256(data).hexdigest() != info.checksum_sha256:
            raise OSError("stored object failed checksum verification")
        return data

    async def path_for_read(self, tenant_id: UUID, key: str) -> Path:
        target = self._path(tenant_id, key)
        info = await self.head(tenant_id, key)
        if info is None:
            raise FileNotFoundError("stored object does not exist")
        if not target.is_file():
            raise FileNotFoundError("stored object does not exist")
        size, checksum = await asyncio.to_thread(_hash_file, target)
        if size != info.size or checksum != info.checksum_sha256:
            raise OSError("stored object failed checksum verification")
        return target

    async def head(self, tenant_id: UUID, key: str) -> ObjectInfo | None:
        target = self._path(tenant_id, key)

        def inspect() -> ObjectInfo | None:
            if not target.is_file():
                return None
            meta_path = self._metadata_path(target)
            if meta_path.is_file():
                return _read_metadata(meta_path, key)
            size, checksum = _hash_file(target)
            return ObjectInfo(
                key=key,
                size=size,
                checksum_sha256=checksum,
                content_type=None,
            )

        return await asyncio.to_thread(inspect)

    async def delete(self, tenant_id: UUID, key: str) -> bool:
        target = self._path(tenant_id, key)

        def remove() -> bool:
            if not target.is_file():
                return False
            target.unlink()
            self._metadata_path(target).unlink(missing_ok=True)
            return True

        return await asyncio.to_thread(remove)

    async def list(self, tenant_id: UUID, prefix: str = "") -> list[ObjectInfo]:
        tenant_root = self._tenant_root(tenant_id)
        if prefix:
            prefix_path = self._path(tenant_id, prefix)
            scan_root = prefix_path if prefix_path.is_dir() else prefix_path.parent
        else:
            scan_root = tenant_root

        def scan() -> list[ObjectInfo]:
            if not scan_root.exists():
                return []
            results: list[ObjectInfo] = []
            for path in scan_root.rglob("*"):
                if not path.is_file() or path.name.startswith("."):
                    continue
                relative = path.relative_to(tenant_root).as_posix()
                if prefix and not relative.startswith(prefix):
                    continue
                meta_path = self._metadata_path(path)
                if meta_path.is_file():
                    results.append(_read_metadata(meta_path, relative))
                else:
                    size, checksum = _hash_file(path)
                    results.append(
                        ObjectInfo(
                            key=relative,
                            size=size,
                            checksum_sha256=checksum,
                            content_type=None,
                        )
                    )
            return sorted(results, key=lambda item: item.key)

        return await asyncio.to_thread(scan)
