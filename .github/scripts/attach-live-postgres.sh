#!/usr/bin/env bash
# Attach Fly Managed Postgres to lloves-lms for live presence.
#
# Manual via .github/workflows/attach-live-postgres.yml. Does not build an
# image and does not rewrite Google OAuth secrets. Sqlite on lloves_data
# stays the catalogue. Live presence reads DATABASE_URL when it is a
# postgres URL (lms/live_presence.py resolve_live_database_url).
#
# Current flyctl `mpg attach` stages DATABASE_URL and does not roll
# machines. `fly secrets deploy` is restart-only against the remote app
# config. If that still leaves /health off postgres, one `fly apps restart`.

set -euo pipefail

APP="${APP:-lloves-lms}"
CLUSTER_NAME="${CLUSTER_NAME:-lloves-live}"
REGION="${REGION:-yyz}"
PLAN="${PLAN:-basic}"
PG_MAJOR="${PG_MAJOR:-16}"
VOLUME_GB="${VOLUME_GB:-10}"
HEALTH_URL="${HEALTH_URL:-https://alc.mckenzian.com/health}"
FLY="${FLY:-flyctl}"
CURL_BIN="${CURL_BIN:-curl}"
CREATE_TIMEOUT="${CREATE_TIMEOUT:-20m}"
HEALTH_TRIES="${HEALTH_TRIES:-18}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-10}"
CLUSTER_TRIES="${CLUSTER_TRIES:-60}"
CLUSTER_INTERVAL="${CLUSTER_INTERVAL:-10}"

# Print a line to the workflow log.
log() {
  printf '%s\n' "$*" >&2
}

# Print the exact create/attach commands and stop. No other Postgres product.
print_exact_cli() {
  local org="$1"
  log "Basic Managed Postgres is a paid plan (2 shared vCPUs, 1 GB, about \$38/month)."
  log "Current flyctl does not ask to confirm that plan when --name, --org, --region, and --plan are set."
  log "If this token cannot authorize the charge, stop. This workflow does not create unmanaged Postgres."
  log "Exact commands:"
  log "  fly mpg create --name ${CLUSTER_NAME} --org ${org} --region ${REGION} --plan ${PLAN} --pg-major-version ${PG_MAJOR} --volume-size ${VOLUME_GB}"
  log "  fly mpg attach <cluster-id> --app ${APP}"
  log "  curl -s ${HEALTH_URL}"
}

# Run flyctl. Postgres URLs stay out of the log. Raw output remains in DEST.
fly_to() {
  local dest="$1"
  shift
  set +e
  "$@" >"$dest" 2>&1
  local rc=$?
  set -e
  sed -E 's#postgres(ql)?://[^[:space:]]+#postgres://[redacted]#g' "$dest" >&2
  return "$rc"
}

# Normalize `fly mpg list --json` into a JSON array file.
write_cluster_array() {
  local src="$1"
  local dest="$2"
  if jq -e 'type == "array" or (type == "object" and (.data | type) == "array")' "$src" >/dev/null 2>&1; then
    jq -c 'if type == "array" then . else .data end' "$src" >"$dest"
  else
    printf '[]' >"$dest"
  fi
}

# Return the cluster id for CLUSTER_NAME, preferring a ready cluster in REGION.
pick_cluster_id() {
  local file="$1"
  jq -r --arg name "$CLUSTER_NAME" --arg region "$REGION" '
    def nm: (.name // .Name // "");
    def rg: (.region // .Region // "");
    def st: ((.status // .Status // "") | ascii_downcase);
    def ident: (.id // .Id // "");
    [ .[] | select(nm == $name) ]
    | (map(select(rg == $region and st == "ready"))
       + map(select(rg == $region))
       + .)
    | .[0] | ident // empty
  ' "$file"
}

# Return the deployment status of DATABASE_URL, or empty when it is absent.
database_url_status() {
  local file="$1"
  if ! jq -e . "$file" >/dev/null 2>&1; then
    printf ''
    return 0
  fi
  jq -r '
    (if type == "array" then .
     elif type == "object" and ((.secrets | type) == "array") then .secrets
     else [] end)
    | map(select((.name // .Name // "") == "DATABASE_URL"))
    | .[0].status // .[0].Status // empty
  ' "$file" | tr -d '[:space:]'
}

# Read live_presence from HEALTH_URL. Body goes to the log.
current_presence() {
  local body presence
  body=$("$CURL_BIN" -fsS --max-time 25 "$HEALTH_URL" || true)
  printf '%s\n' "$body" >&2
  presence=$(printf '%s' "$body" | jq -r '.live_presence // empty' 2>/dev/null || true)
  printf '%s' "$presence" | tr -d '[:space:]'
}

# Poll /health until live_presence is postgres.
wait_postgres() {
  local tries="$1"
  local i presence
  for i in $(seq 1 "$tries"); do
    presence=$(current_presence)
    if [[ "$presence" == "postgres" ]]; then
      log "live_presence=postgres"
      return 0
    fi
    log "live_presence=${presence:-unknown} (${i}/${tries})"
    if [[ "$i" != "$tries" ]]; then
      sleep "$HEALTH_INTERVAL"
    fi
  done
  return 1
}

# Resolve the Fly org slug that owns the LMS app.
resolve_org() {
  local raw="$1"
  jq -r --arg app "$APP" '
    def slug:
      if type == "string" then .
      elif type == "object" then (.Slug // .slug // .RawSlug // .raw_slug // empty)
      else empty end;
    [ .[]
      | select((.Name // .name // "") == $app)
      | (.Organization // .organization // empty | slug)
      | select(. != null and . != "")
    ] | .[0] // empty
  ' "$raw" | tr -d '[:space:]'
}

# List clusters and set CLUSTER_ID when CLUSTER_NAME already exists.
find_cluster() {
  local raw norm
  raw=$(mktemp)
  norm=$(mktemp)
  if ! fly_to "$raw" "$FLY" mpg list --org "$ORG" --json; then
    rm -f "$raw" "$norm"
    return 1
  fi
  write_cluster_array "$raw" "$norm"
  rm -f "$raw"
  CLUSTER_ID=$(pick_cluster_id "$norm")
  rm -f "$norm"
}

# Create lloves-live. On success or a partial create, CLUSTER_ID is set.
create_cluster() {
  local logf rc
  logf=$(mktemp)
  log "Creating Managed Postgres ${CLUSTER_NAME} (${PLAN}, ${REGION})."
  set +e
  timeout "$CREATE_TIMEOUT" "$FLY" mpg create \
    --name "$CLUSTER_NAME" \
    --org "$ORG" \
    --region "$REGION" \
    --plan "$PLAN" \
    --pg-major-version "$PG_MAJOR" \
    --volume-size "$VOLUME_GB" \
    >"$logf" 2>&1
  rc=$?
  set -e
  sed -E 's#postgres(ql)?://[^[:space:]]+#postgres://[redacted]#g' "$logf" >&2
  CLUSTER_ID=$(sed -n 's/^[[:space:]]*ID:[[:space:]]*//p' "$logf" | head -n1 | tr -d '[:space:]')
  if [[ -z "${CLUSTER_ID}" ]]; then
    CLUSTER_ID=$(sed -n 's/.*Waiting for cluster .*(\([^)]*\)).*/\1/p' "$logf" | head -n1 | tr -d '[:space:]')
  fi
  rm -f "$logf"
  if [[ "$rc" -ne 0 && -z "${CLUSTER_ID}" ]]; then
    log "fly mpg create failed (exit ${rc})."
    print_exact_cli "$ORG"
    return "$rc"
  fi
  if [[ "$rc" -ne 0 ]]; then
    log "fly mpg create exited ${rc}; waiting for cluster ${CLUSTER_ID}."
  fi
}

# Block until CLUSTER_ID is ready.
wait_until_ready() {
  local i raw norm status
  for i in $(seq 1 "$CLUSTER_TRIES"); do
    raw=$(mktemp)
    norm=$(mktemp)
    if ! fly_to "$raw" "$FLY" mpg list --org "$ORG" --json; then
      rm -f "$raw" "$norm"
      return 1
    fi
    write_cluster_array "$raw" "$norm"
    rm -f "$raw"
    status=$(jq -r --arg id "$CLUSTER_ID" '
      .[]
      | select((.id // .Id // "") == $id)
      | ((.status // .Status // "") | ascii_downcase)
    ' "$norm" | head -n1 | tr -d '[:space:]')
    rm -f "$norm"
    log "cluster ${CLUSTER_ID} status=${status:-missing} (${i}/${CLUSTER_TRIES})"
    if [[ "$status" == "ready" ]]; then
      return 0
    fi
    if [[ "$status" == "failed" || "$status" == "error" ]]; then
      log "Cluster ${CLUSTER_ID} entered ${status}."
      print_exact_cli "$ORG"
      return 1
    fi
    if [[ "$i" != "$CLUSTER_TRIES" ]]; then
      sleep "$CLUSTER_INTERVAL"
    fi
  done
  log "Cluster ${CLUSTER_ID} did not become ready."
  print_exact_cli "$ORG"
  return 1
}

# Reuse lloves-live or create it, then wait until it is ready.
ensure_cluster() {
  if ! find_cluster; then
    log "fly mpg list failed."
    print_exact_cli "$ORG"
    return 1
  fi
  if [[ -n "${CLUSTER_ID}" ]]; then
    log "Reusing Managed Postgres cluster ${CLUSTER_ID} (${CLUSTER_NAME})."
  else
    create_cluster
  fi
  if [[ -z "${CLUSTER_ID}" ]]; then
    log "No cluster id for ${CLUSTER_NAME}."
    print_exact_cli "$ORG"
    return 1
  fi
  wait_until_ready
}

# Attach the cluster. An existing DATABASE_URL secret is left in place.
attach_cluster() {
  local logf
  logf=$(mktemp)
  if fly_to "$logf" "$FLY" mpg attach "$CLUSTER_ID" --app "$APP"; then
    rm -f "$logf"
    return 0
  fi
  if grep -q "already has DATABASE_URL" "$logf"; then
    log "DATABASE_URL is already set on ${APP}."
    rm -f "$logf"
    return 0
  fi
  rm -f "$logf"
  log "fly mpg attach failed."
  print_exact_cli "$ORG"
  return 1
}

# Refresh DATABASE_URL deployment status into STATUS (global).
refresh_secret_status() {
  local raw
  raw=$(mktemp)
  if ! fly_to "$raw" "$FLY" secrets list --app "$APP" --json; then
    rm -f "$raw"
    return 1
  fi
  STATUS=$(database_url_status "$raw")
  rm -f "$raw"
}

# Roll machines so they load staged secrets. Does not build an image.
deploy_staged_secrets() {
  local raw
  raw=$(mktemp)
  log "Deploying staged secrets on ${APP} (restart only, remote config, no image build)."
  if ! fly_to "$raw" "$FLY" secrets deploy --app "$APP" --detach; then
    rm -f "$raw"
    log "fly secrets deploy failed. Machines were not restarted again."
    return 1
  fi
  rm -f "$raw"
}

# Rolling restart of machines that are already running.
restart_app() {
  local raw
  raw=$(mktemp)
  log "Restarting ${APP} so the machine reloads DATABASE_URL."
  if ! fly_to "$raw" "$FLY" apps restart "$APP"; then
    rm -f "$raw"
    log "fly apps restart failed."
    return 1
  fi
  rm -f "$raw"
}

# Create or reuse the cluster, attach it, and require live_presence=postgres.
main() {
  local presence
  CLUSTER_ID=""
  ORG=""
  STATUS=""

  if [[ -z "${FLY_API_TOKEN:-}" ]]; then
    log "FLY_API_TOKEN is empty. Use the existing repository secret. Do not mint a new token."
    return 1
  fi

  presence=$(current_presence)
  if [[ "$presence" == "postgres" ]]; then
    log "Health already reports live_presence=postgres. No cluster create and no restart."
    return 0
  fi
  log "Health live_presence=${presence:-unknown}."

  local org_file
  org_file=$(mktemp)
  if ! fly_to "$org_file" "$FLY" apps list --json; then
    rm -f "$org_file"
    log "Could not list Fly apps for ${APP}."
    print_exact_cli "<org>"
    return 1
  fi
  ORG=$(resolve_org "$org_file")
  rm -f "$org_file"
  if [[ -z "$ORG" ]]; then
    log "Could not resolve the Fly organization for ${APP}."
    print_exact_cli "<org>"
    return 1
  fi
  log "Organization: ${ORG}"

  if ! refresh_secret_status; then
    log "Could not list secrets on ${APP}."
    return 1
  fi

  if [[ -z "$STATUS" ]]; then
    ensure_cluster
    attach_cluster
    if ! refresh_secret_status; then
      log "Could not list secrets after attach."
      return 1
    fi
    if [[ -z "$STATUS" ]]; then
      log "DATABASE_URL is still absent after attach. Not restarting ${APP}."
      print_exact_cli "$ORG"
      return 1
    fi
  else
    log "DATABASE_URL is already on ${APP} (status=${STATUS}). Not creating another cluster."
  fi

  presence=$(current_presence)
  if [[ "$presence" == "postgres" ]]; then
    log "Attach left live_presence=postgres. No further restart."
    return 0
  fi

  if [[ "$STATUS" != "Deployed" ]]; then
    deploy_staged_secrets
    if wait_postgres "$HEALTH_TRIES"; then
      return 0
    fi
    restart_app
    if wait_postgres "$HEALTH_TRIES"; then
      return 0
    fi
  else
    restart_app
    if wait_postgres "$HEALTH_TRIES"; then
      return 0
    fi
  fi

  log "Refusing success. /health live_presence must be postgres (not sqlite or postgres-down)."
  print_exact_cli "$ORG"
  return 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
