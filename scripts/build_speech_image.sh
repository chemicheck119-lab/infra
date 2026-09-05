#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -lt 1 || "$#" -gt 2 ]]; then
  echo "usage: $0 SPEECH_REPOSITORY [IMAGE_TAG]" >&2
  exit 2
fi

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_project

SPEECH_REPOSITORY="$1"
IMAGE_TAG="${2:-baseline-v1}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/${SPEECH_IMAGE_NAME}:${IMAGE_TAG}"

if [[ ! -f "${SPEECH_REPOSITORY}/Dockerfile" ]]; then
  echo "Dockerfile not found in ${SPEECH_REPOSITORY}" >&2
  exit 1
fi

gcloud builds submit "${SPEECH_REPOSITORY}" \
  --project="${PROJECT_ID}" \
  --tag="${IMAGE}"

echo "${IMAGE}"
