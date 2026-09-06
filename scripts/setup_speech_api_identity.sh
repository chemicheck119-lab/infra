#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )); then
  echo "usage: $0 NEW_SPEECH_API_KEY_FILE" >&2
  exit 2
fi

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_project

KEY_FILE="$1"
if [[ -L "${KEY_FILE}" || ! -f "${KEY_FILE}" ]]; then
  echo "key input must be a regular non-symlink file" >&2
  exit 1
fi
KEY_SIZE="$(wc -c < "${KEY_FILE}" | tr -d ' ')"
if (( KEY_SIZE < 32 || KEY_SIZE > 4096 )); then
  echo "key input must contain between 32 and 4096 bytes" >&2
  exit 1
fi
if gcloud secrets describe "${SPEECH_API_KEY_SECRET}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "refusing to replace or rotate an existing Speech API secret" >&2
  exit 1
fi

if ! gcloud iam service-accounts describe "${SPEECH_API_RUNTIME_SERVICE_ACCOUNT}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SPEECH_API_RUNTIME_SERVICE_ACCOUNT%%@*}" \
    --project="${PROJECT_ID}" \
    --display-name="ChemiCheck119 private Speech API runtime"
fi

gcloud secrets create "${SPEECH_API_KEY_SECRET}" \
  --project="${PROJECT_ID}" \
  --replication-policy=automatic \
  --data-file="${KEY_FILE}"

for principal in \
  "${SPEECH_API_RUNTIME_SERVICE_ACCOUNT}" \
  "${BACKEND_RUNTIME_SERVICE_ACCOUNT}"; do
  gcloud secrets add-iam-policy-binding "${SPEECH_API_KEY_SECRET}" \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${principal}" \
    --role="roles/secretmanager.secretAccessor" >/dev/null
done

echo "Speech runtime identity and Secret Manager binding are ready."
