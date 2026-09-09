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

require_open_billing_account() {
  local billing_account_name
  local billing_enabled
  local billing_account_id
  local billing_account_open

  if ! billing_account_name="$(gcloud billing projects describe "${PROJECT_ID}" \
    --format='value(billingAccountName)' 2>/dev/null)"; then
    echo "unable to verify the project's billing account; refusing GCP mutation" >&2
    return 1
  fi
  if ! billing_enabled="$(gcloud billing projects describe "${PROJECT_ID}" \
    --format='value(billingEnabled)' 2>/dev/null)"; then
    echo "unable to verify whether project billing is enabled; refusing GCP mutation" >&2
    return 1
  fi
  if [[ -z "${billing_account_name}" || \
        ( "${billing_enabled}" != "True" && "${billing_enabled}" != "true" ) ]]; then
    echo "project billing is not enabled; refusing GCP mutation" >&2
    return 1
  fi

  billing_account_id="${billing_account_name#billingAccounts/}"
  if [[ -z "${billing_account_id}" || \
        "${billing_account_id}" == "${billing_account_name}" ]]; then
    echo "project billing account reference is invalid; refusing GCP mutation" >&2
    return 1
  fi
  if ! billing_account_open="$(gcloud billing accounts describe \
    "${billing_account_id}" --format='value(open)' 2>/dev/null)"; then
    echo "unable to verify whether the linked billing account is open; refusing GCP mutation" >&2
    return 1
  fi
  if [[ "${billing_account_open}" != "True" && \
        "${billing_account_open}" != "true" ]]; then
    echo "linked billing account is closed; refusing GCP mutation" >&2
    return 1
  fi
}
