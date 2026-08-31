from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.models import DeviceRecord, normalize_mac

if TYPE_CHECKING:
    from app.companion.life import CareState, OwnerMemory


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


class OwnerMemoryRow(Base):
    __tablename__ = "owner_memory"
    __table_args__ = (UniqueConstraint("device_id", "client_id", name="uq_owner_memory_device"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_name: Mapped[str] = mapped_column(String(48), default="")
    nickname: Mapped[str] = mapped_column(String(48), default="")
    likes: Mapped[str] = mapped_column(String(160), default="")
    locale: Mapped[str] = mapped_column(String(16), default="my-MM")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CareStateRow(Base):
    __tablename__ = "care_state"
    __table_args__ = (UniqueConstraint("device_id", "client_id", name="uq_care_state_device"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    happiness: Mapped[int] = mapped_column(Integer, default=55)
    energy: Mapped[int] = mapped_column(Integer, default=70)
    bond: Mapped[int] = mapped_column(Integer, default=30)
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    chat_count: Mapped[int] = mapped_column(Integer, default=0)
    last_touch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_streak_on: Mapped[str | None] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AchievementRow(Base):
    __tablename__ = "achievements"
    __table_args__ = (
        UniqueConstraint("device_id", "client_id", "code", name="uq_achievement_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    code: Mapped[str] = mapped_column(String(32))
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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


def _life():
    from app.companion import life

    return life


def _memory_from_row(row: OwnerMemoryRow):
    life = _life()
    return life.OwnerMemory(
        device_id=row.device_id,
        client_id=row.client_id,
        owner_name=row.owner_name or "",
        nickname=row.nickname or "",
        likes=row.likes or "",
        locale=row.locale or "my-MM",
        updated_at=row.updated_at,
    )


def _care_from_row(row: CareStateRow):
    life = _life()
    return life.CareState(
        device_id=row.device_id,
        client_id=row.client_id,
        happiness=row.happiness,
        energy=row.energy,
        bond=row.bond,
        streak_days=row.streak_days,
        chat_count=row.chat_count,
        last_touch_at=row.last_touch_at,
        last_streak_on=row.last_streak_on,
        updated_at=row.updated_at,
    )


class PostgresCompanionStore:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def get_memory(self, device_id: str, client_id: str) -> OwnerMemory:
        device_id, client_id = normalize_mac(device_id), client_id.strip()
        async with self._factory() as session:
            result = await session.execute(
                select(OwnerMemoryRow).where(
                    OwnerMemoryRow.device_id == device_id,
                    OwnerMemoryRow.client_id == client_id,
                )
            )
            row = result.scalar_one_or_none()
            return _memory_from_row(row) if row else _life().empty_memory(device_id, client_id)

    async def set_memory(self, memory: OwnerMemory) -> OwnerMemory:
        device_id, client_id = normalize_mac(memory.device_id), memory.client_id.strip()
        stamp = _life().utcnow()
        async with self._factory() as session:
            result = await session.execute(
                select(OwnerMemoryRow).where(
                    OwnerMemoryRow.device_id == device_id,
                    OwnerMemoryRow.client_id == client_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = OwnerMemoryRow(
                    device_id=device_id,
                    client_id=client_id,
                    owner_name=memory.owner_name,
                    nickname=memory.nickname,
                    likes=memory.likes,
                    locale=memory.locale or "my-MM",
                    updated_at=stamp,
                )
                session.add(row)
            else:
                row.owner_name = memory.owner_name
                row.nickname = memory.nickname
                row.likes = memory.likes
                row.locale = memory.locale or row.locale or "my-MM"
                row.updated_at = stamp
            await session.commit()
            await session.refresh(row)
            return _memory_from_row(row)

    async def get_care(self, device_id: str, client_id: str) -> CareState:
        device_id, client_id = normalize_mac(device_id), client_id.strip()
        async with self._factory() as session:
            result = await session.execute(
                select(CareStateRow).where(
                    CareStateRow.device_id == device_id,
                    CareStateRow.client_id == client_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                fresh = _life().empty_care(device_id, client_id)
                row = CareStateRow(
                    device_id=device_id,
                    client_id=client_id,
                    happiness=fresh.happiness,
                    energy=fresh.energy,
                    bond=fresh.bond,
                    streak_days=fresh.streak_days,
                    chat_count=fresh.chat_count,
                    last_touch_at=fresh.last_touch_at,
                    last_streak_on=fresh.last_streak_on,
                    updated_at=fresh.updated_at or _life().utcnow(),
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return _care_from_row(row)

    async def apply_care(self, device_id: str, client_id: str, kind: str) -> CareState:
        device_id, client_id = normalize_mac(device_id), client_id.strip()
        async with self._factory() as session:
            result = await session.execute(
                select(CareStateRow).where(
                    CareStateRow.device_id == device_id,
                    CareStateRow.client_id == client_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                state = _life().empty_care(device_id, client_id)
            else:
                state = _care_from_row(row)
            _life().apply_care(state, kind)
            if row is None:
                row = CareStateRow(
                    device_id=device_id,
                    client_id=client_id,
                    happiness=state.happiness,
                    energy=state.energy,
                    bond=state.bond,
                    streak_days=state.streak_days,
                    chat_count=state.chat_count,
                    last_touch_at=state.last_touch_at,
                    last_streak_on=state.last_streak_on,
                    updated_at=state.updated_at or _life().utcnow(),
                )
                session.add(row)
            else:
                row.happiness = state.happiness
                row.energy = state.energy
                row.bond = state.bond
                row.streak_days = state.streak_days
                row.chat_count = state.chat_count
                row.last_touch_at = state.last_touch_at
                row.last_streak_on = state.last_streak_on
                row.updated_at = state.updated_at or _life().utcnow()
            await session.commit()
            await session.refresh(row)
            return _care_from_row(row)

    async def decay_all(self) -> list[CareState]:
        life = _life()
        now = life.utcnow()
        changed: list[CareState] = []
        async with self._factory() as session:
            result = await session.execute(select(CareStateRow))
            rows = list(result.scalars().all())
            for row in rows:
                state = _care_from_row(row)
                before = (state.happiness, state.energy, state.bond)
                life.decay_care(state, now=now)
                if (state.happiness, state.energy, state.bond) == before:
                    continue
                row.happiness = state.happiness
                row.energy = state.energy
                row.bond = state.bond
                row.updated_at = state.updated_at or now
                changed.append(state)
            if changed:
                await session.commit()
        return changed

    async def list_achievements(self, device_id: str, client_id: str) -> list[str]:
        device_id, client_id = normalize_mac(device_id), client_id.strip()
        async with self._factory() as session:
            result = await session.execute(
                select(AchievementRow.code).where(
                    AchievementRow.device_id == device_id,
                    AchievementRow.client_id == client_id,
                )
            )
            return [item[0] for item in result.all()]

    async def unlock_achievement(self, device_id: str, client_id: str, code: str) -> bool:
        device_id, client_id = normalize_mac(device_id), client_id.strip()
        async with self._factory() as session:
            session.add(
                AchievementRow(
                    device_id=device_id,
                    client_id=client_id,
                    code=code,
                    unlocked_at=_life().utcnow(),
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
            return True
