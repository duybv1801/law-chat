"""Auth CLI: `uv run python -m app.auth.cli create-admin <email>` (password via prompt)."""

import asyncio

import typer
from pydantic import ValidationError

from app.auth import service
from app.auth.schemas import RegisterRequest
from app.core.errors import AppError
from app.db.session import SessionLocal, engine

cli = typer.Typer(add_completion=False, no_args_is_help=True)


@cli.callback()
def main() -> None:
    """Auth management commands."""


async def _create_admin(email: str, password: str) -> None:
    try:
        async with SessionLocal() as session:
            await service.create_user(session, email, password, role="admin")
    finally:
        await engine.dispose()


@cli.command("create-admin")
def create_admin(email: str) -> None:
    """Create an admin account. Fails if the email is already registered."""
    password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    try:
        body = RegisterRequest(email=email, password=password)
    except ValidationError as exc:
        fields = ", ".join(str(err["loc"][0]) for err in exc.errors())
        typer.echo(f"Invalid {fields} (password needs at least 8 characters).", err=True)
        raise typer.Exit(1) from None
    try:
        asyncio.run(_create_admin(body.email, body.password))
    except AppError as exc:
        typer.echo(f"Error: {exc.message}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Admin {body.email} created.")


if __name__ == "__main__":
    cli()
