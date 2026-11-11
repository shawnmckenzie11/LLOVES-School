#!/usr/bin/env bash
# Install headless LibreOffice for the slide-conversion tests.
#
# Runner DNS to archive.ubuntu.com, azure.archive.ubuntu.com, and
# security.ubuntu.com has hung ``apt-get update`` for more than 20 minutes
# (MCK-470). This script skips the install when ``soffice`` or
# ``libreoffice`` is already on PATH, bounds every apt call, and gives the
# whole attempt a hard timeout. A non-zero exit means the binary is still
# missing. The workflow continues, and the tests that render decks skip
# themselves when ``soffice_bin()`` is empty.

set -u

have_soffice() {
  # True when a headless LibreOffice binary is already on PATH.
  command -v soffice >/dev/null 2>&1 || command -v libreoffice >/dev/null 2>&1
}

release_stale_locks() {
  # Drop apt/dpkg locks left behind by a killed download.
  #
  # Only runs when apt and dpkg are not alive, so a real unpack is left
  # alone. ``sudo -n`` fails instead of waiting for a password.
  if pgrep -x apt-get >/dev/null 2>&1 || pgrep -x apt >/dev/null 2>&1 || pgrep -x dpkg >/dev/null 2>&1; then
    echo "apt or dpkg still running; leaving locks in place"
    return 0
  fi
  timeout --signal=TERM --kill-after=5 15 sudo -n rm -f \
    /var/lib/apt/lists/lock \
    /var/cache/apt/archives/lock \
    /var/lib/dpkg/lock-frontend \
    /var/lib/dpkg/lock \
    || true
}

if have_soffice; then
  echo "LibreOffice already on PATH: $(command -v soffice || command -v libreoffice)"
  exit 0
fi

if ! command -v timeout >/dev/null 2>&1; then
  echo "::warning title=LibreOffice unavailable::timeout(1) is missing, so apt was not started."
  exit 1
fi

# Re-exec under a hard cap so a resolver that ignores apt's own timeouts
# cannot outlive this step. The child inherits the marker and does the install.
# LIBREOFFICE_HARD_TIMEOUT is seconds; CI leaves it at 180.
HARD_TIMEOUT="${LIBREOFFICE_HARD_TIMEOUT:-180}"
if [[ "${LIBREOFFICE_INSTALL_INNER:-}" != "1" ]]; then
  export LIBREOFFICE_INSTALL_INNER=1
  timeout --signal=TERM --kill-after=10 "${HARD_TIMEOUT}" "$0"
  status=$?
  if have_soffice; then
    echo "LibreOffice is available: $(command -v soffice || command -v libreoffice)"
    exit 0
  fi
  release_stale_locks
  echo "::warning title=LibreOffice unavailable::Install exited ${status}. Slide tests that need soffice will skip."
  exit "${status}"
fi

set -o pipefail

APT_OPTIONS=(
  -o Acquire::Retries=3
  -o Acquire::http::Timeout=20
  -o Acquire::https::Timeout=20
)
export DEBIAN_FRONTEND=noninteractive

package_known() {
  # True when local apt lists already name libreoffice-impress.
  #
  # A known candidate can be installed without refreshing every Ubuntu
  # mirror. A later attempt still updates if that install fails.
  local candidate
  candidate="$(apt-cache policy libreoffice-impress 2>/dev/null | awk '/Candidate:/ {print $2; exit}' || true)"
  [[ -n "${candidate}" && "${candidate}" != "(none)" ]]
}

install_once() {
  # One bounded update (when needed) and one bounded install.
  #
  # Args:
  #   $1: attempt number, starting at 1.
  #
  # Returns:
  #   0 when apt-get install exits 0. The caller checks for soffice.
  #
  # LIBREOFFICE_UPDATE_TIMEOUT and LIBREOFFICE_INSTALL_TIMEOUT are seconds.
  # CI leaves them at 30 and 90.
  local attempt="$1"
  local update_timeout="${LIBREOFFICE_UPDATE_TIMEOUT:-30}"
  local install_timeout="${LIBREOFFICE_INSTALL_TIMEOUT:-90}"
  if [[ "${attempt}" == "1" ]] && package_known; then
    echo "apt lists already name libreoffice-impress; skipping apt-get update"
  else
    echo "apt-get update (attempt ${attempt})"
    timeout --signal=TERM --kill-after=5 "${update_timeout}" sudo -n apt-get "${APT_OPTIONS[@]}" update || return $?
  fi
  echo "apt-get install (attempt ${attempt})"
  timeout --signal=TERM --kill-after=5 "${install_timeout}" sudo -n apt-get "${APT_OPTIONS[@]}" install -y --no-install-recommends \
    libreoffice-impress \
    fonts-liberation \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea || return $?
}

for attempt in 1 2 3; do
  echo "LibreOffice install attempt ${attempt}/3"
  if install_once "${attempt}"; then
    if have_soffice; then
      echo "Installed $(command -v soffice || command -v libreoffice)"
      exit 0
    fi
    echo "apt-get install exited 0 but soffice is not on PATH"
  else
    echo "LibreOffice install attempt ${attempt} failed"
  fi
  release_stale_locks
  sleep 2
done

echo "LibreOffice install failed after 3 attempts"
exit 1
