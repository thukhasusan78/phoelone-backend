from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_s: float = 60.0) -> None:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > window_s:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        bucket.append(now)


limiter = SlidingWindowLimiter()


def client_key(request: Request, device_id: str | None = None) -> str:
    if device_id:
        return f"dev:{device_id.lower()}"
    forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    return f"ip:{ip}"
