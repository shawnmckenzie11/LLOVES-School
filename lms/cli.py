"""Flask CLI commands for one-off school maintenance (MCK-204)."""

from __future__ import annotations

import os
from pathlib import Path

import click
from flask import Flask

from catchup_backfill import backfill_catchup_from_snapshots


def register_cli(app: Flask) -> None:
    """Attach maintenance commands to the application."""

    @app.cli.command("backfill-catchup")
    @click.option("--course", default=None, help="Ontario course code, e.g. MCF3M")
    @click.option("--module", "live_module", default=None, help="Live module, e.g. M2")
    @click.option("--slot", "live_slot", default=None, help="Live slot, e.g. C1")
    @click.option(
        "--apply",
        is_flag=True,
        default=False,
        help="Write rows (default is dry-run only).",
    )
    @click.option(
        "--pins",
        is_flag=True,
        default=False,
        help="When used with --apply, also assign default PINs for students missing one.",
    )
    def backfill_catchup(
        course: str | None,
        live_module: str | None,
        live_slot: str | None,
        apply: bool,
        pins: bool,
    ) -> None:
        """Backfill catch-up links from live_result_snapshots (dry-run by default)."""
        if (os.getenv("FLASK_ENV") or "").lower() == "production" and apply:
            raise click.ClickException(
                "Refusing --apply against production. Run on a local copy of the database."
            )
        school = app.config["SCHOOL_DB"]
        report = backfill_catchup_from_snapshots(
            school,
            course=course,
            module=live_module,
            slot=live_slot,
            apply=apply,
        )
        for line in report.summary_lines():
            click.echo(line)
        if apply and pins:
            secret = str(app.secret_key or "")
            summary = school.ensure_default_pins_for_classes(
                class_ids={r.class_id for r in report.runs if r.action == "wrote"},
                secret=secret,
            )
            click.echo(
                f"default PINs: assigned={summary['assigned']} "
                f"skipped_existing={summary['skipped_existing']}"
            )


def main() -> None:
    """Entry point for ``python -m cli`` against the default database."""
    from app import create_app

    application = create_app()
    register_cli(application)
    db = Path(os.getenv("LLOVES_DB") or application.config["SCHOOL_DB"].db_path)
    if (os.getenv("FLASK_ENV") or "").lower() == "production":
        raise SystemExit("Refusing CLI with FLASK_ENV=production")
    application.cli.main(prog_name="flask")


if __name__ == "__main__":
    main()
