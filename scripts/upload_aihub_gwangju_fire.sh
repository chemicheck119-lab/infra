#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: $0 AIHUB_DATA_ROOT DATA_PIPELINE_REPOSITORY" >&2
  exit 2
fi

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_project
require_open_billing_account

AIHUB_DATA_ROOT="$1"
DATA_PIPELINE_REPOSITORY="$2"
TRAIN_AUDIO="${AIHUB_DATA_ROOT}/Training/1.원천데이터/TS_광주_화재.zip"
TRAIN_LABELS="${AIHUB_DATA_ROOT}/Training/2.라벨링데이터/TL_광주_화재.zip"
VALIDATION_AUDIO="${AIHUB_DATA_ROOT}/Validation/1.원천데이터/VS_광주_화재.zip"
VALIDATION_LABELS="${AIHUB_DATA_ROOT}/Validation/2.라벨링데이터/VL_광주_화재.zip"
EVALUATION_MANIFEST="${DATA_PIPELINE_REPOSITORY}/data/manifests/aihub-71768-gwangju-fire-validation.json"

for source in "${TRAIN_AUDIO}" "${TRAIN_LABELS}" \
  "${VALIDATION_AUDIO}" "${VALIDATION_LABELS}"; do
  if [[ ! -f "${source}" ]]; then
    echo "required archive not found: ${source}" >&2
    exit 1
  fi
done
if [[ ! -f "${EVALUATION_MANIFEST}" ]]; then
  echo "evaluation manifest not found: ${EVALUATION_MANIFEST}" >&2
  exit 1
fi

DESTINATION="gs://${ML_BUCKET}/raw/aihub/71768/gwangju-fire"
gcloud storage cp \
  "${TRAIN_AUDIO}" "${TRAIN_LABELS}" \
  "${VALIDATION_AUDIO}" "${VALIDATION_LABELS}" \
  "${DESTINATION}/"
gcloud storage cp "${EVALUATION_MANIFEST}" \
  "gs://${ML_BUCKET}/manifests/aihub-71768-gwangju-fire-validation.json"

echo "Uploaded four restricted archives and their evaluation manifest."
