from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clipos.contracts.future import ClipCandidateContract
from clipos.storage import LocalObjectStorage, UnsafeStorageKey

pytestmark = pytest.mark.unit


async def test_local_storage_is_tenant_scoped_and_atomic(tmp_path) -> None:
    storage = LocalObjectStorage(tmp_path / "objects")
    tenant_a, tenant_b = uuid4(), uuid4()
    info = await storage.put(
        tenant_a, "fixtures/object.txt", b"alpha payload", content_type="text/plain"
    )
    assert info.size == len(b"alpha payload")
    assert await storage.get(tenant_a, "fixtures/object.txt") == b"alpha payload"
    assert await storage.head(tenant_b, "fixtures/object.txt") is None
    assert [item.key for item in await storage.list(tenant_a)] == ["fixtures/object.txt"]
    assert await storage.delete(tenant_a, "fixtures/object.txt") is True
    assert await storage.delete(tenant_a, "fixtures/object.txt") is False


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a/../../b", "a\\b", ""])
async def test_local_storage_rejects_unsafe_keys(tmp_path, key: str) -> None:
    storage = LocalObjectStorage(tmp_path / "objects")
    with pytest.raises(UnsafeStorageKey):
        await storage.put(uuid4(), key, b"unsafe")


def test_future_candidate_contract_rejects_invalid_time_order() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="end_seconds"):
        ClipCandidateContract(
            id=uuid4(),
            tenant_id=uuid4(),
            asset_id=uuid4(),
            state="proposed",
            start_seconds=20,
            end_seconds=10,
            algorithm_version="future-v1",
            evidence={},
            created_at=now,
            updated_at=now,
        )


def test_future_contract_forbids_unknown_fields() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="extra"):
        ClipCandidateContract(
            id=uuid4(),
            tenant_id=uuid4(),
            asset_id=uuid4(),
            state="proposed",
            start_seconds=1,
            end_seconds=10,
            algorithm_version="future-v1",
            evidence={},
            created_at=now,
            updated_at=now,
            model_says_authorized=True,
        )
