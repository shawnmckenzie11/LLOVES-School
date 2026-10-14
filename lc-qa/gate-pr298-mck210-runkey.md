# Gate bag — MCK-210 run-key repair (post 8aee7e3)

## Done

1. **Repair without session rows:** `repair-catchup --class-id <id> --run-key <prefix|full>` (repeatable pairs); snapshots + `resolve_snapshot_run_key` / `get_catchup_repair_run_row`.
2. **No start-as-end:** Offering-backed classes require `course_offerings.live_end_time` for repair (`require_live_class_schedule_for_class` with `offering_end_required=True`). Catalog end may still classify at End Live; not `classes.time`.
3. **`sync-offering-live-schedules`:** Dry-run default; `--apply --i-know-this-is-prod` + `OFFERING_SCHEDULES:<n>`; reads `lms/seeds/live_class_schedules.json` only at sync time.
4. **Prod commands:** `lc-qa/mck210-prod-repair-commands.md` — `python -m cli`, `fly ssh console --pty`, `<machine>` placeholder; end times first, then per-run repair; skip 7656 / link 2.
5. **Tests:** `test_mck210_catchup_repair.py` — multi-class, UTC, offerings without end until sync, run-key repair after session wipe.

## Ops order

1. Sync offerings 10, 11, 12 end times.
2. Repair: class 9 `1b01` (reopen link 5), class 10 `9a20` (mint), then remaining keys per commands file.
