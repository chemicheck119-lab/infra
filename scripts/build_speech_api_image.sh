#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 2 )); then
  echo "usage: $0 SPEECH_REPOSITORY SPEECH_COMMIT_SHA" >&2
  exit 2
fi

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_command git
require_project
require_open_billing_account

SPEECH_REPOSITORY="$1"
SPEECH_REVISION="$2"
BUILD_CONFIG="${INFRA_DIRECTORY}/config/cloudbuild-speech-api.yaml"

if [[ ! "${SPEECH_REVISION}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "speech revision must be an immutable 40-character commit" >&2
  exit 1
fi
if [[ ! -d "${SPEECH_REPOSITORY}/.git" || -L "${SPEECH_REPOSITORY}" ]]; then
  echo "speech repository must be a non-symlink Git worktree" >&2
  exit 1
fi
if [[ ! -f "${SPEECH_REPOSITORY}/Dockerfile.api" || \
      ! -f "${SPEECH_REPOSITORY}/.dockerignore" ]]; then
  echo "Speech API Dockerfile or deny-all build context policy is missing" >&2
  exit 1
fi
if [[ -n "$(git -C "${SPEECH_REPOSITORY}" status --porcelain)" ]]; then
  echo "speech repository must be clean before a release image build" >&2
  exit 1
fi
if [[ "$(git -C "${SPEECH_REPOSITORY}" rev-parse HEAD)" != "${SPEECH_REVISION}" ]]; then
  echo "speech repository HEAD does not match the requested revision" >&2
  exit 1
fi
REMOTE_MAIN_REVISION="$({
  git -C "${SPEECH_REPOSITORY}" ls-remote origin refs/heads/main | awk '{print $1}'
})"
if [[ "${REMOTE_MAIN_REVISION}" != "${SPEECH_REVISION}" ]]; then
  echo "speech revision must equal the currently advertised main commit" >&2
  exit 1
fi

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/${SPEECH_API_IMAGE_NAME}:${SPEECH_REVISION}"
if gcloud artifacts docker images describe "${IMAGE}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "refusing to overwrite an existing immutable commit tag: ${IMAGE}" >&2
  exit 1
fi

BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
gcloud builds submit "${SPEECH_REPOSITORY}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --config="${BUILD_CONFIG}" \
  --substitutions="_IMAGE=${IMAGE},_VCS_REF=${SPEECH_REVISION},_BUILD_DATE=${BUILD_DATE}" \
  --timeout=1800s

gcloud artifacts docker images describe "${IMAGE}" \
  --project="${PROJECT_ID}" \
  --format='value(image_summary.fully_qualified_digest)'
