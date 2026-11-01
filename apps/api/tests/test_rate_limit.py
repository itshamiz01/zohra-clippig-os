import pytest

from clipos.core.middleware import MemoryRateLimiter

pytestmark = pytest.mark.unit


async def test_memory_rate_limiter_enforces_window_budget() -> None:
    limiter = MemoryRateLimiter(2)
    assert await limiter.allow("client-a") == (True, 1)
    assert await limiter.allow("client-a") == (True, 0)
    assert await limiter.allow("client-a") == (False, 0)
    assert await limiter.allow("client-b") == (True, 1)
