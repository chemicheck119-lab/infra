#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_command git
require_command python3
require_project

if (( $# != 2 )); then
  echo "usage: $0 SPEECH_COMMIT_SHA COST_QUOTE_JSON" >&2
  exit 1
fi

SPEECH_REVISION="$1"
COST_QUOTE_PATH="$2"
if [[ ! "${SPEECH_REVISION}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "speech revision must be an immutable 40-character commit" >&2
  exit 1
fi
if [[ -L "${COST_QUOTE_PATH}" || ! -f "${COST_QUOTE_PATH}" ]]; then
  echo "cost quote must be a regular non-symlink file" >&2
  exit 1
fi
python3 "${SCRIPT_DIRECTORY}/validate_lora_cost_quote.py" "${COST_QUOTE_PATH}"

INSTANCE_NAME="chemicheck119-lora-$(date -u +%Y%m%d-%H%M%S)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-${SPEECH_REVISION:0:12}"
COST_QUOTE_GCS_URI="${LORA_OUTPUT_GCS_PREFIX}/inputs/${RUN_ID}.cost-quote.json"
RUN_OUTPUT_GCS_PREFIX="${LORA_OUTPUT_GCS_PREFIX}/runs/${RUN_ID}"
CREATED=false

cleanup_instance() {
  if [[ "${CREATED}" == true ]] && gcloud compute instances describe \
    "${INSTANCE_NAME}" --zone="${LORA_ZONE}" --project="${PROJECT_ID}" \
    >/dev/null 2>&1; then
    gcloud compute instances delete "${INSTANCE_NAME}" \
      --zone="${LORA_ZONE}" --project="${PROJECT_ID}" --quiet >/dev/null
  fi
}
trap cleanup_instance EXIT INT TERM

if gcloud compute instances describe "${INSTANCE_NAME}" \
  --zone="${LORA_ZONE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "refusing to reuse an existing instance" >&2
  exit 1
fi

gcloud storage cp --if-generation-match=0 \
  "${COST_QUOTE_PATH}" "${COST_QUOTE_GCS_URI}" >/dev/null

CREATED=true
gcloud compute instances create "${INSTANCE_NAME}" \
  --project="${PROJECT_ID}" \
  --zone="${LORA_ZONE}" \
  --machine-type="${LORA_MACHINE_TYPE}" \
  --accelerator="type=${LORA_GPU_TYPE},count=1" \
  --provisioning-model=STANDARD \
  --maintenance-policy=TERMINATE \
  --no-restart-on-failure \
  --max-run-duration="${LORA_MAX_RUN_SECONDS}s" \
  --instance-termination-action=DELETE \
  --image-project="${LORA_IMAGE_PROJECT}" \
  --image="${LORA_IMAGE_NAME}" \
  --boot-disk-size="${LORA_BOOT_DISK_GB}GB" \
  --boot-disk-type=pd-balanced \
  --boot-disk-auto-delete \
  --service-account="${ML_RUNNER_SERVICE_ACCOUNT}" \
  --scopes=https://www.googleapis.com/auth/devstorage.read_write \
  --shielded-secure-boot \
  --metadata="enable-oslogin=TRUE,block-project-ssh-keys=TRUE,speech-revision=${SPEECH_REVISION},speech-repository-url=${SPEECH_REPOSITORY_URL},data-gcs-prefix=${LORA_DATA_GCS_PREFIX},cost-quote-gcs-uri=${COST_QUOTE_GCS_URI},output-gcs-prefix=${RUN_OUTPUT_GCS_PREFIX},train-timeout-seconds=${LORA_TRAIN_TIMEOUT_SECONDS}" \
  --metadata-from-file="startup-script=${SCRIPT_DIRECTORY}/startup_whisper_lora.sh" \
  >/dev/null
echo "bounded LoRA instance created: ${INSTANCE_NAME}"
echo "output prefix: ${RUN_OUTPUT_GCS_PREFIX}/result"

LAST_STATUS=""
while true; do
  if ! STATUS="$(gcloud compute instances describe "${INSTANCE_NAME}" \
    --zone="${LORA_ZONE}" --project="${PROJECT_ID}" \
    --format='value(status)' 2>/dev/null)"; then
    break
  fi
  if [[ "${STATUS}" != "${LAST_STATUS}" ]]; then
    echo "instance status: ${STATUS}"
    LAST_STATUS="${STATUS}"
  fi
  if [[ "${STATUS}" == "TERMINATED" || "${STATUS}" == "STOPPED" ]]; then
    break
  fi
  sleep 15
done

REPORT_URI="${RUN_OUTPUT_GCS_PREFIX}/result/training-report.json"
SUCCESS=true
if ! gcloud storage objects describe "${REPORT_URI}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  SUCCESS=false
  echo "training report was not created; inspect serial-port output" >&2
  gcloud compute instances get-serial-port-output "${INSTANCE_NAME}" \
    --zone="${LORA_ZONE}" --project="${PROJECT_ID}" || true
fi

cleanup_instance
CREATED=false

if [[ "${SUCCESS}" != true ]]; then
  exit 1
fi

echo "bounded LoRA training completed: ${REPORT_URI}"
