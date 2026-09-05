#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIRECTORY="$(cd "${SCRIPT_DIRECTORY}/.." && pwd)"

# shellcheck disable=SC1091
source "${INFRA_DIRECTORY}/config/ml.env"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "required command not found: $1" >&2
    exit 1
  }
}

require_project() {
  local active_project
  active_project="$(gcloud config get-value project 2>/dev/null)"
  if [[ "${active_project}" != "${PROJECT_ID}" ]]; then
    echo "active gcloud project must be ${PROJECT_ID}; got ${active_project}" >&2
    exit 1
  fi
}
