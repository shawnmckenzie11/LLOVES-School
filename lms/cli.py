"""Flask CLI commands for one-off school maintenance (MCK-204 / MCK-206)."""

from __future__ import annotations

import logging
import os
import sys
import click
from flask import Flask

from catchup_backfill import backfill_catchup_from_snapshots, repair_catchup

logger = logging.getLogger(__name__)


def _is_production_env() -> bool:
    """True on Fly or when ``FLASK_ENV=production`` (empty env alone is not prod)."""
    if str(os.getenv("FLY_APP_NAME") or "").strip():
        return True
    return (os.getenv("FLASK_ENV") or "").lower() == "production"


def _confirmation_phrase(course: str, live_module: str, live_slot: str) -> str:
    """Typed confirmation string for a scoped prod backfill."""
    return f"{str(course).strip().upper()}/{str(live_module).strip().upper()}/{str(live_slot).strip().upper()}"


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
    @click.option(
        "--i-know-this-is-prod",
        "prod_override",
        is_flag=True,
        default=False,
        help="One-shot override to run --apply on production after confirmation.",
    )
    def backfill_catchup(
        course: str | None,
        live_module: str | None,
        live_slot: str | None,
        apply: bool,
        pins: bool,
        prod_override: bool,
    ) -> None:
        """Backfill catch-up links from live_result_snapshots (dry-run by default)."""
        if pins and not apply:
            raise click.ClickException("--pins requires --apply.")
        if apply and _is_production_env() and not prod_override:
            raise click.ClickException(
                "Refusing --apply on production without --i-know-this-is-prod."
            )
        if apply and prod_override:
            missing = [
                name
                for name, value in (
                    ("--course", course),
                    ("--module", live_module),
                    ("--slot", live_slot),
                )
                if not str(value or "").strip()
            ]
            if missing:
                raise click.ClickException(
                    "Production apply requires --course, --module, and --slot "
                    f"(missing {', '.join(missing)})."
                )
        school = app.config["SCHOOL_DB"]
        if apply and prod_override:
            preview = backfill_catchup_from_snapshots(
                school,
                course=course,
                module=live_module,
                slot=live_slot,
                apply=False,
            )
            for line in preview.summary_lines():
                click.echo(line)
            phrase = _confirmation_phrase(course or "", live_module or "", live_slot or "")
            typed = click.prompt("Type course/module/slot to confirm", type=str).strip()
            if typed != phrase:
                raise click.ClickException("Confirmation did not match; aborting.")
        report = backfill_catchup_from_snapshots(
            school,
            course=course,
            module=live_module,
            slot=live_slot,
            apply=apply,
        )
        for line in report.summary_lines():
            click.echo(line)
        pins_assigned = 0
        if apply and pins:
            secret = str(app.secret_key or "")
            summary = school.ensure_default_pins_for_classes(
                class_ids={
                    r.class_id
                    for r in report.runs
                    if "participation" in r.action and r.action.startswith("wrote")
                },
                secret=secret,
            )
            pins_assigned = int(summary["assigned"])
            click.echo(
                f"default PINs: assigned={pins_assigned} "
                f"skipped_existing={summary['skipped_existing']}"
            )
        if apply and prod_override:
            wrote = [
                r for r in report.runs if "participation" in r.action and r.action.startswith("wrote")
            ]
            audit_line = (
                "catchup_backfill_audit course=%s module=%s slot=%s runs=%s items=%s "
                "answers=%s pins_assigned=%s"
                % (
                    str(course or "").upper(),
                    str(live_module or "").upper(),
                    str(live_slot or "").upper(),
                    len(wrote),
                    sum(int(r.items) for r in wrote),
                    sum(int(r.answers) for r in wrote),
                    pins_assigned,
                )
            )
            click.echo(audit_line)
            logger.warning(audit_line)

    @app.cli.command("repair-catchup")
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
        "--i-know-this-is-prod",
        "prod_override",
        is_flag=True,
        default=False,
        help="One-shot override to run --apply on production after confirmation.",
    )
    def repair_catchup_cmd(
        course: str | None,
        live_module: str | None,
        live_slot: str | None,
        apply: bool,
        prod_override: bool,
    ) -> None:
        """Re-classify runs and fix shared catch-up links (dry-run by default)."""
        if apply and _is_production_env() and not prod_override:
            raise click.ClickException(
                "Refusing --apply on production without --i-know-this-is-prod."
            )
        if apply and prod_override:
            missing = [
                name
                for name, value in (
                    ("--course", course),
                    ("--module", live_module),
                    ("--slot", live_slot),
                )
                if not str(value or "").strip()
            ]
            if missing:
                raise click.ClickException(
                    "Production apply requires --course, --module, and --slot "
                    f"(missing {', '.join(missing)})."
                )
        school = app.config["SCHOOL_DB"]
        if apply and prod_override:
            preview = repair_catchup(
                school,
                course=course,
                module=live_module,
                slot=live_slot,
                apply=False,
            )
            for line in preview.summary_lines():
                click.echo(line)
            phrase = _confirmation_phrase(course or "", live_module or "", live_slot or "")
            typed = click.prompt("Type course/module/slot to confirm", type=str).strip()
            if typed != phrase:
                raise click.ClickException("Confirmation did not match; aborting.")
        report = repair_catchup(
            school,
            course=course,
            module=live_module,
            slot=live_slot,
            apply=apply,
        )
        for line in report.summary_lines():
            click.echo(line)
        if apply and prod_override:
            changed = [r for r in report.runs if r.action not in ("skip", "skip link")]
            audit_line = (
                "catchup_repair_audit course=%s module=%s slot=%s actions=%s"
                % (
                    str(course or "").upper(),
                    str(live_module or "").upper(),
                    str(live_slot or "").upper(),
                    len(changed),
                )
            )
            click.echo(audit_line)
            logger.warning(audit_line)


def main() -> None:
    """Entry point for ``python -m cli`` against the default database."""
    from app import create_app

    application = create_app()
    register_cli(application)
    if _is_production_env():
        allowed_cmd = len(sys.argv) > 1 and sys.argv[1] in (
            "backfill-catchup",
            "repair-catchup",
        )
        if not allowed_cmd:
            raise SystemExit(
                "Refusing CLI on production except: flask backfill-catchup … "
                "or flask repair-catchup …"
            )
    application.cli.main(prog_name="flask")


if __name__ == "__main__":
    main()
