#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 FULL_IMAGE_URI" >&2
  exit 2
fi

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_project
require_open_billing_account

IMAGE="$1"
INPUT_PREFIX="gs://${ML_BUCKET}/raw/aihub/71768/gwangju-fire"
OUTPUT_PREFIX="gs://${ML_BUCKET}/experiments/speech/aihub-71768-gwangju-fire"
MANIFEST="gs://${ML_BUCKET}/manifests/aihub-71768-gwangju-fire-validation.json"

gcloud run jobs deploy "${SPEECH_JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${ML_RUNNER_SERVICE_ACCOUNT}" \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=0 \
  --task-timeout=2h \
  --cpu=4 \
  --memory=8Gi \
  --set-env-vars="WHISPER_MODEL=small,MODEL_CACHE=/opt/whisper-models" \
  --args="--audio-archive=${INPUT_PREFIX}/VS_광주_화재.zip","--label-archive=${INPUT_PREFIX}/VL_광주_화재.zip","--dataset-manifest=${MANIFEST}","--hotwords-file=/app/config/domain_hotwords.txt","--model=small","--device=cpu","--compute-type=int8","--cpu-threads=4","--model-cache=/opt/whisper-models","--local-files-only","--output-dir=/tmp/outputs","--gcs-output-prefix=${OUTPUT_PREFIX}"

echo "Job deployed but not executed. Use run_speech_eval.sh explicitly."
