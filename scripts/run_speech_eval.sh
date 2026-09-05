#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -gt 1 ]]; then
  echo "usage: $0 [RECORD_LIMIT]" >&2
  exit 2
fi

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_project

INPUT_PREFIX="gs://${ML_BUCKET}/raw/aihub/71768/gwangju-fire"
OUTPUT_PREFIX="gs://${ML_BUCKET}/experiments/speech/aihub-71768-gwangju-fire"
MANIFEST="gs://${ML_BUCKET}/manifests/aihub-71768-gwangju-fire-validation.json"
JOB_ARGUMENTS=(
  "--audio-archive=${INPUT_PREFIX}/VS_광주_화재.zip"
  "--label-archive=${INPUT_PREFIX}/VL_광주_화재.zip"
  "--dataset-manifest=${MANIFEST}"
  "--hotwords-file=/app/config/domain_hotwords.txt"
  "--model=small"
  "--device=cpu"
  "--compute-type=int8"
  "--cpu-threads=4"
  "--model-cache=/opt/whisper-models"
  "--local-files-only"
  "--output-dir=/tmp/outputs"
  "--gcs-output-prefix=${OUTPUT_PREFIX}"
)
if [[ "$#" -eq 1 ]]; then
  if [[ ! "$1" =~ ^[1-9][0-9]*$ ]]; then
    echo "RECORD_LIMIT must be a positive integer" >&2
    exit 2
  fi
  JOB_ARGUMENTS+=("--limit=$1")
fi

ARGUMENT_LIST="$(IFS=,; echo "${JOB_ARGUMENTS[*]}")"
gcloud run jobs update "${SPEECH_JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --args="${ARGUMENT_LIST}" >/dev/null

gcloud run jobs execute "${SPEECH_JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait
