#!/bin/sh
set -eu

: "${RELEASE_SHA:?RELEASE_SHA is required}"
: "${PREVIOUS_GOOD_SHA:?PREVIOUS_GOOD_SHA is required}"
: "${API_DEPLOY_HOOK_URL:?API_DEPLOY_HOOK_URL is required}"
: "${DASHBOARD_DEPLOY_HOOK_URL:?DASHBOARD_DEPLOY_HOOK_URL is required}"
: "${API_HEALTH_URL:?API_HEALTH_URL is required, for example https://api.example/health}"
: "${DASHBOARD_HEALTH_URL:?DASHBOARD_HEALTH_URL is required, for example https://pay.example/sign-in}"

DEPLOY_TIMEOUT_SECONDS="${DEPLOY_TIMEOUT_SECONDS:-900}"
POLL_SECONDS="${POLL_SECONDS:-10}"

hook_url() {
    case "$1" in
        *\?*) printf '%s&ref=%s' "$1" "$2" ;;
        *) printf '%s?ref=%s' "$1" "$2" ;;
    esac
}

trigger_release() {
    target_sha="$1"
    curl -fsS -X POST "$(hook_url "$API_DEPLOY_HOOK_URL" "$target_sha")" >/dev/null
    curl -fsS -X POST "$(hook_url "$DASHBOARD_DEPLOY_HOOK_URL" "$target_sha")" >/dev/null
}

wait_for_api_release() {
    expected_sha="$1"
    deadline=$(( $(date +%s) + DEPLOY_TIMEOUT_SECONDS ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        body="$(curl -fsS "$API_HEALTH_URL" 2>/dev/null || true)"
        observed="$(
            printf '%s' "$body" |
                python3 -c 'import json, sys
try:
    print(json.load(sys.stdin).get("release_sha", ""))
except Exception:
    print("")'
        )"
        if [ "$observed" = "$expected_sha" ]; then
            return 0
        fi
        sleep "$POLL_SECONDS"
    done
    return 1
}

smoke_test() {
    expected_sha="$1"
    wait_for_api_release "$expected_sha" &&
        curl -fsS "$DASHBOARD_HEALTH_URL" >/dev/null
}

printf 'Deploying LynxPay release %s\n' "$RELEASE_SHA"
trigger_release "$RELEASE_SHA"
if smoke_test "$RELEASE_SHA"; then
    printf 'Deployment passed health gates for %s\n' "$RELEASE_SHA"
    exit 0
fi

printf 'Deployment failed health gates; rolling back to %s\n' "$PREVIOUS_GOOD_SHA" >&2
trigger_release "$PREVIOUS_GOOD_SHA"
if smoke_test "$PREVIOUS_GOOD_SHA"; then
    printf 'Rollback completed for %s\n' "$PREVIOUS_GOOD_SHA" >&2
else
    printf 'CRITICAL: automatic rollback did not pass health gates\n' >&2
fi
exit 1

