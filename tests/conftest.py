import pytest

from app.api.rate_limit import limiter

pytest_plugins: list[str] = []


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    limiter._hits.clear()
    yield
