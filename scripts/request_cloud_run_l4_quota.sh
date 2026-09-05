#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 GOOGLE_ACCOUNT_EMAIL" >&2
  exit 2
fi

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_project

CONTACT_EMAIL="$1"
PREFERENCE_ID="chemicheck119-l4-${REGION}"
QUOTA_ID="NvidiaL4GpuAllocNoZonalRedundancyPerProjectRegion"

if gcloud beta quotas preferences describe "${PREFERENCE_ID}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud beta quotas preferences describe "${PREFERENCE_ID}" \
    --project="${PROJECT_ID}" \
    --format="yaml(name,quotaConfig,dimensions,reconciling)"
  exit 0
fi

gcloud beta quotas preferences create \
  --project="${PROJECT_ID}" \
  --preference-id="${PREFERENCE_ID}" \
  --service=run.googleapis.com \
  --quota-id="${QUOTA_ID}" \
  --dimensions="region=${REGION}" \
  --preferred-value=1 \
  --email="${CONTACT_EMAIL}" \
  --justification="Academic contest evaluation of Korean emergency-call ASR. One manual, retry-disabled Cloud Run Job needs a single L4; no production traffic or autoscaling."
