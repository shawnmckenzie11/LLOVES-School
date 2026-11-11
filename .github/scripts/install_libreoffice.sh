#!/usr/bin/env bash
# Install headless LibreOffice for the slide-conversion tests.
#
# GitHub runners point apt at azure.archive.ubuntu.com. When that mirror
# stops answering, each fetch returns Ign after Acquire::http::Timeout and
# a bare apt-get update can sit for tens of minutes (MCK-470). This script
# skips the install when soffice is already on PATH. Otherwise it tries the
# runner's current mirror, then rewrites azure.archive.ubuntu.com to
# archive.ubuntu.com, then to mirror.arizona.edu. Every apt call is bounded.
# A non-zero exit means the binary is still missing. The workflow continues,
# and the tests that need soffice or the metric fonts skip themselves.

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

apt_dir() {
  # Directory that holds sources.list, deb822 files, and the mirror list.
  #
  # LIBREOFFICE_APT_ROOT is empty in CI. Tests point it at a fixture tree
  # so the rewrite can be checked without touching the machine's apt config.
  echo "${LIBREOFFICE_APT_ROOT:-}/etc/apt"
}

rewrite_archive_host() {
  # Point Ubuntu archive URIs at one host.
  #
  # Args:
  #   $1: Host that should serve /ubuntu, such as archive.ubuntu.com.
  #
  # Edits sources.list, every deb822 ``*.sources`` and legacy ``*.list``
  # under sources.list.d, and apt-mirrors.txt (the file a
  # ``mirror+file:`` URI reads). ``azure.archive.ubuntu.com`` is replaced
  # before the shorter ``archive.ubuntu.com`` so the Azure name is not
  # rewritten into ``azure.<new-host>``. security.ubuntu.com is left alone.
  local host="$1"
  local dir file
  dir="$(apt_dir)"
  shopt -s nullglob
  local files=()
  [[ -f "${dir}/sources.list" ]] && files+=("${dir}/sources.list")
  [[ -f "${dir}/apt-mirrors.txt" ]] && files+=("${dir}/apt-mirrors.txt")
  local extra
  for extra in "${dir}/sources.list.d/"*.list "${dir}/sources.list.d/"*.sources; do
    [[ -f "${extra}" ]] && files+=("${extra}")
  done
  shopt -u nullglob
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "No apt source files under ${dir}; leaving mirrors unchanged"
    return 0
  fi
  echo "Switching Ubuntu archive host to ${host} in ${#files[@]} file(s)"
  local -a sed_expr=(
    -e "s|azure\\.archive\\.ubuntu\\.com|${host}|g"
    -e "s|archive\\.ubuntu\\.com|${host}|g"
  )
  for file in "${files[@]}"; do
    if [[ -w "${file}" ]]; then
      sed -i "${sed_expr[@]}" "${file}"
    else
      timeout --signal=TERM --kill-after=5 20 sudo -n sed -i "${sed_expr[@]}" "${file}" || return $?
    fi
  done
}

# Tests set this to a host and exit after the rewrite, so the fixture can
# be checked without running apt. CI leaves it unset.
if [[ -n "${LIBREOFFICE_REWRITE_ONLY:-}" ]]; then
  rewrite_archive_host "${LIBREOFFICE_REWRITE_ONLY}"
  exit $?
fi

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
# LIBREOFFICE_HARD_TIMEOUT is seconds. CI leaves it at 600 (10 minutes).
HARD_TIMEOUT="${LIBREOFFICE_HARD_TIMEOUT:-600}"
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

# Attempt 1 keeps the runner mirror. Later attempts leave Azure.
MIRROR_HOSTS=(
  ""
  "archive.ubuntu.com"
  "mirror.arizona.edu"
)

package_known() {
  # True when local apt lists already name libreoffice-impress.
  #
  # A known candidate can be installed without refreshing every Ubuntu
  # mirror. A later attempt still updates after the host is rewritten.
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
  # CI leaves them at 60 and 240. 240 is enough for about 100 MB. A dead
  # mirror still dies inside that cap because each HTTP request is 20s
  # and the ``timeout`` command kills the process.
  local attempt="$1"
  local update_timeout="${LIBREOFFICE_UPDATE_TIMEOUT:-60}"
  local install_timeout="${LIBREOFFICE_INSTALL_TIMEOUT:-240}"
  local host="${MIRROR_HOSTS[$((attempt - 1))]:-}"
  if [[ -n "${host}" ]]; then
    rewrite_archive_host "${host}" || echo "Mirror rewrite to ${host} failed; continuing with the current sources"
  fi
  if [[ "${attempt}" == "1" ]] && package_known; then
    echo "apt lists already name libreoffice-impress; skipping apt-get update"
  else
    echo "apt-get update (attempt ${attempt})"
    # A sick security mirror must not skip the install. Lists that did
    # arrive are enough to fetch libreoffice-impress from the archive host.
    timeout --signal=TERM --kill-after=5 "${update_timeout}" sudo -n apt-get "${APT_OPTIONS[@]}" update \
      || echo "apt-get update failed (attempt ${attempt}); trying install with the lists that arrived"
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
