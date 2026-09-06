#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )); then
  echo "usage: $0 PRIVATE_OUTPUT_JSON" >&2
  exit 2
fi

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_command python3
require_project

OUTPUT_PATH="$1"
if [[ "${OUTPUT_PATH}" != /* || -e "${OUTPUT_PATH}" || -L "${OUTPUT_PATH}" ]]; then
  echo "output must be a new absolute path" >&2
  exit 1
fi
OUTPUT_PARENT="$(dirname "${OUTPUT_PATH}")"
mkdir -p "${OUTPUT_PARENT}"
chmod 700 "${OUTPUT_PARENT}"
TEMP_DIRECTORY="$(mktemp -d "${OUTPUT_PARENT}/.speech-audit.XXXXXX")"
trap 'rm -rf "${TEMP_DIRECTORY}"' EXIT
chmod 700 "${TEMP_DIRECTORY}"

gcloud run services describe "${SPEECH_API_SERVICE_NAME}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format=json \
  > "${TEMP_DIRECTORY}/service.json"
gcloud run services get-iam-policy "${SPEECH_API_SERVICE_NAME}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format=json \
  > "${TEMP_DIRECTORY}/iam.json"
chmod 600 "${TEMP_DIRECTORY}/service.json" "${TEMP_DIRECTORY}/iam.json"

python3 "${SCRIPT_DIRECTORY}/audit_private_speech_api.py" \
  --service-json "${TEMP_DIRECTORY}/service.json" \
  --iam-json "${TEMP_DIRECTORY}/iam.json" \
  --backend-service-account "${BACKEND_RUNTIME_SERVICE_ACCOUNT}" \
  --runtime-service-account "${SPEECH_API_RUNTIME_SERVICE_ACCOUNT}" \
  --output "${OUTPUT_PATH}"
chmod 600 "${OUTPUT_PATH}"
