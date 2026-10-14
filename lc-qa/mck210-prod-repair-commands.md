# MCK-210 prod repair commands (PR #298)

Use Fly SSH with a PTY. Replace `<machine>` with the running `lloves-lms` machine id (`fly machines list -a lloves-lms`). All commands run from `/app/lms` inside the container.

**Order:** sync offering end times first, then repair each run by `--class-id` / `--run-key` (no `live_class_sessions` / session-id SQL).

## 1) Offering `live_end_time` (offerings 10, 11, 12)

Dry run (default):

```bash
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli sync-offering-live-schedules --offering 10 --offering 11 --offering 12'"
```

Apply (after dry run looks correct):

```bash
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli sync-offering-live-schedules --offering 10 --offering 11 --offering 12 --apply --i-know-this-is-prod'"
```

Confirm with: `OFFERING_SCHEDULES:<count>` (count printed in dry run).

## 2) Repair catch-up (one run per invocation)

Dry run each pair, then apply with `--apply --i-know-this-is-prod` and confirm `RUN:<class_id>:<run_key>`.

| class_id | run_key | Notes |
|---------|---------|--------|
| 9 | 1b01 | Reopen catch-up link id **5** |
| 10 | 9a20 | Mint new open link |
| 9 | 8312 | Test run; discard test link only |
| 10 | 7e0c | Real MCR3U M2 C4 |
| 10 | 4373 | Real MCR3U M2 C4 |
| 10 | 3b4b | Test only |
| 10 | a5d4 | Test only |
| 8 | d20a | Real MCF3M M2 C4 |
| 8 | 0dae | Test run |
| 8 | 2af3 | Real MCF3M M2 C4 |

**Do not repair** run `7656` or touch catch-up link id **2**.

### Example dry run (class 9, run 1b01)

```bash
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 9 --run-key 1b01'"
```

### Example apply (class 9, run 1b01)

```bash
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 9 --run-key 1b01 --apply --i-know-this-is-prod'"
```

### Full repair sequence (dry run, in order)

```bash
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 9 --run-key 1b01'"
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 10 --run-key 9a20'"
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 9 --run-key 8312'"
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 10 --run-key 7e0c'"
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 10 --run-key 4373'"
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 10 --run-key 3b4b'"
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 10 --run-key a5d4'"
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 8 --run-key d20a'"
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 8 --run-key 0dae'"
fly ssh console -a lloves-lms --machine <machine> --pty -C "sh -c 'cd /app/lms && python -m cli repair-catchup --class-id 8 --run-key 2af3'"
```

Repeat each line with `--apply --i-know-this-is-prod` and the per-run confirmation phrase after ops review.
