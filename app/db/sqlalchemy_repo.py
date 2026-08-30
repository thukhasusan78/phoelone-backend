from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.models import DeviceRecord


class Base(DeclarativeBase):
    pass


class DeviceRow(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("device_id", "client_id", name="uq_device_client"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    serial_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    locale: Mapped[str] = mapped_column(String(16), default="my-MM")
    token_hash: Mapped[str] = mapped_column(String(64))
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    activation_code: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    activation_challenge: Mapped[str | None] = mapped_column(String(64), nullable=True)
    activation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    token_ciphertext: Mapped[str | None] = mapped_column(String(512), nullable=True)


class AuditRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[str] = mapped_column(String(32), default="")
    detail: Mapped[str] = mapped_column(String(512), default="")


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def _to_record(row: DeviceRow) -> DeviceRecord:
    return DeviceRecord(
        id=row.id,
        device_id=row.device_id,
        client_id=row.client_id,
        serial_number=row.serial_number,
        status=row.status,
        locale=row.locale,
        token_hash=row.token_hash,
        token_version=row.token_version,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        last_user_agent=row.last_user_agent,
        activation_code=row.activation_code,
        activation_challenge=row.activation_challenge,
        activation_expires_at=row.activation_expires_at,
        token_ciphertext=row.token_ciphertext,
    )


class PostgresDeviceRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def get(self, device_id: str, client_id: str) -> DeviceRecord | None:
        async with self._factory() as session:
            result = await session.execute(
                select(DeviceRow).where(
                    DeviceRow.device_id == device_id,
                    DeviceRow.client_id == client_id,
                )
            )
            row = result.scalar_one_or_none()
            return _to_record(row) if row else None

    async def upsert(self, record: DeviceRecord) -> DeviceRecord:
        async with self._factory() as session:
            result = await session.execute(
                select(DeviceRow).where(
                    DeviceRow.device_id == record.device_id,
                    DeviceRow.client_id == record.client_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = DeviceRow(
                    device_id=record.device_id,
                    client_id=record.client_id,
                    serial_number=record.serial_number,
                    status=record.status,
                    locale=record.locale,
                    token_hash=record.token_hash,
                    token_version=record.token_version,
                    created_at=record.created_at,
                    last_seen_at=record.last_seen_at,
                    last_user_agent=record.last_user_agent,
                    activation_code=record.activation_code,
                    activation_challenge=record.activation_challenge,
                    activation_expires_at=record.activation_expires_at,
                    token_ciphertext=record.token_ciphertext,
                )
                session.add(row)
            else:
                row.serial_number = record.serial_number
                row.status = record.status
                row.locale = record.locale
                row.token_hash = record.token_hash
                row.token_version = record.token_version
                row.last_seen_at = record.last_seen_at
                row.last_user_agent = record.last_user_agent
                row.activation_code = record.activation_code
                row.activation_challenge = record.activation_challenge
                row.activation_expires_at = record.activation_expires_at
                row.token_ciphertext = record.token_ciphertext
            await session.commit()
            await session.refresh(row)
            return _to_record(row)

    async def list_devices(self) -> list[DeviceRecord]:
        async with self._factory() as session:
            result = await session.execute(select(DeviceRow).order_by(DeviceRow.id))
            return [_to_record(r) for r in result.scalars().all()]

    async def set_status(self, device_id: str, client_id: str, status: str) -> None:
        async with self._factory() as session:
            await session.execute(
                update(DeviceRow)
                .where(DeviceRow.device_id == device_id, DeviceRow.client_id == client_id)
                .values(status=status)
            )
            await session.commit()

    async def touch(
        self,
        device_id: str,
        client_id: str,
        *,
        user_agent: str | None = None,
    ) -> None:
        values: dict[str, object] = {"last_seen_at": datetime.now(timezone.utc)}
        if user_agent is not None:
            values["last_user_agent"] = user_agent[:256]
        async with self._factory() as session:
            await session.execute(
                update(DeviceRow)
                .where(DeviceRow.device_id == device_id, DeviceRow.client_id == client_id)
                .values(**values)
            )
            await session.commit()

    async def get_by_activation_code(self, code: str) -> DeviceRecord | None:
        if not code:
            return None
        async with self._factory() as session:
            result = await session.execute(
                select(DeviceRow).where(
                    DeviceRow.activation_code == code,
                    DeviceRow.status == "pending",
                )
            )
            row = result.scalar_one_or_none()
            return _to_record(row) if row else None
