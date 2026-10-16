"""Flask CLI commands for one-off school maintenance (MCK-204 / MCK-206 / MCK-210)."""

from __future__ import annotations

import logging
import os
import sys
import click
from flask import Flask

from catchup_backfill import backfill_catchup_from_snapshots, repair_catchup
from catchup_schedule import CatchupScheduleError

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

    @app.cli.command("sync-offering-live-schedules")
    @click.option(
        "--offering",
        "offering_ids",
        multiple=True,
        type=int,
        help="Limit to specific course_offerings.id values (repeatable).",
    )
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
    def sync_offering_live_schedules_cmd(
        offering_ids: tuple[int, ...],
        apply: bool,
        prod_override: bool,
    ) -> None:
        """Fill missing offering live_end_time from seeds/live_class_schedules.json."""
        if apply and _is_production_env() and not prod_override:
            raise click.ClickException(
                "Refusing --apply on production without --i-know-this-is-prod."
            )
        school = app.config["SCHOOL_DB"]
        ids = [int(x) for x in offering_ids] if offering_ids else None
        plans = school.plan_sync_offering_live_schedules(
            offering_ids=ids if ids else None
        )
        mode = "APPLIED" if apply else "DRY RUN"
        click.echo(f"sync offering live schedules ({mode})")
        if not plans:
            click.echo("  (no offerings missing live_end_time with seed defaults)")
            return
        for row in plans:
            click.echo(
                "  offering_id=%s %s section=%s -> live_days=%s live_end_time=%s"
                % (
                    row["offering_id"],
                    row["ontario_code"],
                    row["section_index"],
                    row["live_days"] or "(keep)",
                    row["live_end_time"],
                )
            )
        if apply and prod_override:
            preview_count = len(plans)
            typed = click.prompt(
                f"Type OFFERING_SCHEDULES:{preview_count} to confirm apply",
                type=str,
            ).strip()
            if typed != f"OFFERING_SCHEDULES:{preview_count}":
                raise click.ClickException("Confirmation did not match; aborting.")
        if apply:
            updated = school.apply_sync_offering_live_schedules(plans)
            audit = f"offering_schedule_sync_audit updated={updated}"
            click.echo(audit)
            logger.warning(audit)

    @app.cli.command("repair-catchup")
    @click.option(
        "--run",
        "session_ids",
        multiple=True,
        type=int,
        help="Ended live_class_sessions.id (optional; prod usually has none).",
    )
    @click.option(
        "--class-id",
        "class_ids",
        multiple=True,
        type=int,
        help="Class id paired with each --run-key (repeatable).",
    )
    @click.option(
        "--run-key",
        "run_keys",
        multiple=True,
        help="Snapshot run_key prefix/full key paired with --class-id.",
    )
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
        session_ids: tuple[int, ...],
        class_ids: tuple[int, ...],
        run_keys: tuple[str, ...],
        apply: bool,
        prod_override: bool,
    ) -> None:
        """Re-classify and fix catch-up links for snapshot runs (dry-run default)."""
        ids = [int(x) for x in session_ids]
        pairs = list(zip(class_ids, run_keys))
        if pairs and len(class_ids) != len(run_keys):
            raise click.ClickException(
                "Each --class-id must pair with one --run-key "
                f"(got {len(class_ids)} class ids, {len(run_keys)} run keys)."
            )
        if not ids and not pairs:
            raise click.ClickException(
                "Provide --class-id/--run-key pairs and/or --run session ids."
            )
        if apply and _is_production_env() and not prod_override:
            raise click.ClickException(
                "Refusing --apply on production without --i-know-this-is-prod."
            )
        school = app.config["SCHOOL_DB"]
        targets = [(int(cid), str(key)) for cid, key in pairs]
        if apply and prod_override:
            preview = repair_catchup(
                school,
                session_ids=ids,
                run_targets=targets,
                apply=False,
            )
            for line in preview.summary_lines():
                click.echo(line)
            for cid, key in targets:
                phrase = f"RUN:{cid}:{key}"
                typed = click.prompt(
                    f"Type {phrase} to confirm apply", type=str
                ).strip()
                if typed != phrase:
                    raise click.ClickException("Confirmation did not match; aborting.")
        try:
            report = repair_catchup(
                school,
                session_ids=ids,
                run_targets=targets,
                apply=apply,
            )
        except CatchupScheduleError as exc:
            raise click.ClickException(str(exc)) from exc
        for line in report.summary_lines():
            click.echo(line)
        errors = [r for r in report.runs if r.action == "error"]
        if errors:
            raise click.ClickException(errors[0].detail or "repair failed")
        if apply and prod_override:
            changed = [
                r
                for r in report.runs
                if not r.action.startswith("skip") and "skip participation" not in r.action
            ]
            audit_line = "catchup_repair_audit targets=%s actions=%s" % (
                len(targets) + len(ids),
                len(changed),
            )
            click.echo(audit_line)
            logger.warning(audit_line)

    @app.cli.command("whats-new")
    @click.option(
        "--id",
        "release_ids",
        multiple=True,
        help="whats_new_releases id to hide (repeatable). Not the Dashboard's deploy-time id.",
    )
    @click.option(
        "--apply",
        is_flag=True,
        default=False,
        help="Hide the given ids. Without this flag, nothing is written.",
    )
    @click.option(
        "--i-know-this-is-prod",
        "prod_override",
        is_flag=True,
        default=False,
        help="Required with --apply when this process is pointed at production.",
    )
    def whats_new_cmd(release_ids: tuple[str, ...], apply: bool, prod_override: bool) -> None:
        """List What's New rows, and hide ids with --apply (dry-run by default).

        Deploy-sourced rows stay hidden across boots. A committed row is
        rewritten from releases.json on the next boot, so hiding that id
        does not stick. Safe to run twice: an id that is already hidden
        stays hidden.
        """
        import whats_new_store

        if apply and not release_ids:
            raise click.ClickException("Pass at least one --id to hide. --apply with no ids writes nothing.")
        if apply and _is_production_env() and not prod_override:
            raise click.ClickException(
                "Refusing --apply on production without --i-know-this-is-prod."
            )
        school = app.config["SCHOOL_DB"]
        mode = "APPLIED" if apply else "DRY RUN"
        click.echo(f"What's new ({mode}) db={school.db_path}")

        def _read() -> list[dict]:
            """Stored What's New rows, including hidden ones."""
            with school._lock:
                return whats_new_store.list_stored_releases(school.conn)

        def _echo_rows(rows: list[dict]) -> None:
            """Print one block per stored release."""
            if not rows:
                click.echo("(no stored What's new rows)")
                return
            for row in rows:
                flag = "yes" if row["hidden"] else "no"
                click.echo(
                    f"id={row['id']} day={row['day']} source={row['source']} hidden={flag}"
                )
                for text in row["items"]:
                    click.echo(f"  - {text}")
                if not row["items"]:
                    click.echo("  - (no items)")

        rows = _read()
        _echo_rows(rows)
        if not release_ids:
            return
        by_id = {row["id"]: row for row in rows}
        if not apply:
            would, already, missing = [], [], []
            seen: set[str] = set()
            for raw in release_ids:
                rid = str(raw or "").strip()
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                row = by_id.get(rid)
                if row is None:
                    missing.append(rid)
                elif row["hidden"]:
                    already.append(rid)
                else:
                    would.append(rid)
                    if row["source"] != "deploy":
                        click.echo(
                            f"note: {rid} is source={row['source']} and a boot sync "
                            "rewrites it from releases.json"
                        )
            click.echo("would hide: " + (", ".join(would) if would else "(none)"))
            click.echo("already hidden: " + (", ".join(already) if already else "(none)"))
            click.echo("missing: " + (", ".join(missing) if missing else "(none)"))
            click.echo("no rows written")
            if missing:
                raise click.exceptions.Exit(1)
            return
        with school._lock:
            result = whats_new_store.hide_stored_releases(school.conn, list(release_ids))
        for rid in result["hidden"] + result["already"]:
            row = by_id.get(rid)
            if row is not None and row["source"] != "deploy":
                click.echo(
                    f"note: {rid} is source={row['source']} and a boot sync "
                    "rewrites it from releases.json"
                )
        click.echo("hidden: " + (", ".join(result["hidden"]) if result["hidden"] else "(none)"))
        click.echo(
            "already hidden: " + (", ".join(result["already"]) if result["already"] else "(none)")
        )
        click.echo("missing: " + (", ".join(result["missing"]) if result["missing"] else "(none)"))
        audit = (
            "whats_new_hide_audit hidden=%s already=%s missing=%s"
            % (len(result["hidden"]), len(result["already"]), len(result["missing"]))
        )
        click.echo(audit)
        logger.warning(audit)
        if result["missing"]:
            raise click.exceptions.Exit(1)


def main() -> None:
    """Entry point for ``python -m cli`` against the default database."""
    from app import create_app

    application = create_app()
    register_cli(application)
    if _is_production_env():
        allowed_cmd = len(sys.argv) > 1 and sys.argv[1] in (
            "backfill-catchup",
            "repair-catchup",
            "sync-offering-live-schedules",
            "whats-new",
        )
        if not allowed_cmd:
            raise SystemExit(
                "Refusing CLI on production except: backfill-catchup, "
                "repair-catchup, sync-offering-live-schedules, whats-new"
            )
    application.cli.main(prog_name="python -m cli")


if __name__ == "__main__":
    main()
