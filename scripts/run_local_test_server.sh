#!/bin/sh
# Local-only LMS for the Mac teacher and student-view bots (:8788).
# Sets LLOVES_TEST_LOGIN and binds the same HOST the boot check requires.
# Does not deploy and does not change Google ID token verification.
set -eu
cd "$(dirname "$0")/.."

if [ -n "${FLY_APP_NAME:-}" ]; then
  echo "FLY_APP_NAME is set; refusing to start the test staff login." >&2
  exit 1
fi
if [ "${FLASK_ENV:-}" = "production" ]; then
  echo "FLASK_ENV=production; refusing to start the test staff login." >&2
  exit 1
fi

# One loopback address. HOST is what create_app checks. --host is the bind.
HOST=127.0.0.1
export HOST
export LLOVES_TEST_LOGIN=1
export PORT="${PORT:-8788}"

echo "LLOVES test server: http://${HOST}:${PORT}/auth/test-staff" >&2
exec python3 lms/app.py --host "$HOST" --port "$PORT"
