from __future__ import annotations

import asyncio
from typing import Optional

import typer
from sqlalchemy import text

from app.auth.service import AuthService
from app.config import get_settings
from app.db.memory import InMemoryDeviceRepository
from app.db.models import normalize_mac

cli = typer.Typer(help="Operator tools for Phoe Lone device provisioning")


def _repo_and_settings():
    settings = get_settings()
    if settings.uses_memory_db:
        typer.echo("DATABASE_URL is memory:// ; provisioned devices will not persist.")
        return InMemoryDeviceRepository(), settings

    from app.db.sqlalchemy_repo import PostgresDeviceRepository, create_engine, session_factory

    engine = create_engine(settings.database_url)
    factory = session_factory(engine)
    return PostgresDeviceRepository(factory), settings


@cli.command()
def provision(
    device_id: str = typer.Option(..., help="Wi-Fi MAC, e.g. aa:bb:cc:dd:ee:ff"),
    client_id: str = typer.Option(..., help="Client-Id UUID from the device"),
    serial_number: Optional[str] = typer.Option(None, help="Optional eFuse serial"),
    locale: str = typer.Option("my-MM"),
) -> None:
    """Pre-provision a device and print a one-time WebSocket token."""

    async def _run() -> None:
        repo, settings = _repo_and_settings()
        auth = AuthService(repo, settings)
        record, token = await auth.provision(
            device_id, client_id, serial_number=serial_number, locale=locale, rotate=True
        )
        typer.echo(f"device_id={record.device_id}")
        typer.echo(f"client_id={record.client_id}")
        typer.echo(f"status={record.status}")
        typer.echo(f"token_version={record.token_version}")
        typer.echo(f"token={token}")
        typer.echo("OTA version checks echo this same token; use `phoe-lone rotate` to replace it.")

    asyncio.run(_run())


@cli.command("list")
def list_devices() -> None:
    async def _run() -> None:
        repo, _ = _repo_and_settings()
        rows = await repo.list_devices()
        if not rows:
            typer.echo("no devices")
            return
        for row in rows:
            typer.echo(
                f"{row.device_id} {row.client_id} status={row.status} v={row.token_version}"
            )

    asyncio.run(_run())


@cli.command()
def disable(
    device_id: str = typer.Option(...),
    client_id: str = typer.Option(...),
) -> None:
    async def _run() -> None:
        repo, settings = _repo_and_settings()
        auth = AuthService(repo, settings)
        await auth.disable(device_id, client_id)
        typer.echo(f"disabled {normalize_mac(device_id)} {client_id}")

    asyncio.run(_run())


@cli.command()
def rotate(
    device_id: str = typer.Option(...),
    client_id: str = typer.Option(...),
) -> None:
    async def _run() -> None:
        repo, settings = _repo_and_settings()
        auth = AuthService(repo, settings)
        record, token = await auth.provision(device_id, client_id, rotate=True)
        typer.echo(f"rotated {record.device_id} version={record.token_version}")
        typer.echo(f"token={token}")

    asyncio.run(_run())


@cli.command()
def dbcheck() -> None:
    async def _run() -> None:
        settings = get_settings()
        if settings.uses_memory_db:
            typer.echo("memory backend ok")
            return
        from app.db.sqlalchemy_repo import create_engine

        engine = create_engine(settings.database_url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        typer.echo("postgres ok")

    asyncio.run(_run())


app = cli

if __name__ == "__main__":
    cli()
