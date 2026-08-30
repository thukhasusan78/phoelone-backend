from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import DeviceRecord


class InMemoryDeviceRepository:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], DeviceRecord] = {}
        self._seq = 0

    async def get(self, device_id: str, client_id: str) -> DeviceRecord | None:
        return self._rows.get((device_id, client_id))

    async def upsert(self, record: DeviceRecord) -> DeviceRecord:
        key = (record.device_id, record.client_id)
        existing = self._rows.get(key)
        if existing is None:
            self._seq += 1
            record.id = self._seq
        else:
            record.id = existing.id
        self._rows[key] = record
        return record

    async def list_devices(self) -> list[DeviceRecord]:
        return list(self._rows.values())

    async def set_status(self, device_id: str, client_id: str, status: str) -> None:
        row = self._rows.get((device_id, client_id))
        if row:
            row.status = status

    async def touch(
        self,
        device_id: str,
        client_id: str,
        *,
        user_agent: str | None = None,
    ) -> None:
        row = self._rows.get((device_id, client_id))
        if not row:
            return
        row.last_seen_at = datetime.now(timezone.utc)
        if user_agent is not None:
            row.last_user_agent = user_agent[:256]

    async def get_by_activation_code(self, code: str) -> DeviceRecord | None:
        if not code:
            return None
        for row in self._rows.values():
            if row.activation_code == code and row.status == "pending":
                return row
        return None
