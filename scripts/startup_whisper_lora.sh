#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

metadata_value() {
  curl --fail --silent --show-error \
    --header 'Metadata-Flavor: Google' \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"
}

shutdown_instance() {
  shutdown -h now >/dev/null 2>&1 || true
}

trap shutdown_instance EXIT

SPEECH_REVISION="$(metadata_value speech-revision)"
SPEECH_REPOSITORY_URL="$(metadata_value speech-repository-url)"
DATA_GCS_PREFIX="$(metadata_value data-gcs-prefix)"
COST_QUOTE_GCS_URI="$(metadata_value cost-quote-gcs-uri)"
OUTPUT_GCS_PREFIX="$(metadata_value output-gcs-prefix)"
TRAIN_TIMEOUT_SECONDS="$(metadata_value train-timeout-seconds)"

if [[ ! "${SPEECH_REVISION}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "speech revision must be an immutable 40-character commit" >&2
  exit 1
fi
if [[ "${SPEECH_REPOSITORY_URL}" != "https://github.com/chemicheck119-lab/speech-service.git" ]]; then
  echo "speech repository URL does not match the registered public source" >&2
  exit 1
fi
if [[ ! "${DATA_GCS_PREFIX}" =~ ^gs://[^/]+/.+[^/]$ ]]; then
  echo "data GCS prefix is invalid" >&2
  exit 1
fi
if [[ ! "${COST_QUOTE_GCS_URI}" =~ ^gs://[^/]+/.+\.json$ ]]; then
  echo "cost quote GCS URI is invalid" >&2
  exit 1
fi
if [[ ! "${OUTPUT_GCS_PREFIX}" =~ ^gs://[^/]+/.+[^/]$ ]]; then
  echo "output GCS prefix is invalid" >&2
  exit 1
fi
if [[ ! "${TRAIN_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]] || (( TRAIN_TIMEOUT_SECONDS > 9900 )); then
  echo "training timeout exceeds the upload-reserved limit" >&2
  exit 1
fi

for command in curl git gcloud python3 timeout; do
  command -v "${command}" >/dev/null 2>&1 || {
    echo "required command not found: ${command}" >&2
    exit 1
  }
done

WORK_ROOT=/var/lib/chemicheck119-lora
SOURCE_ROOT="${WORK_ROOT}/speech-service"
ARTIFACT_ROOT="${WORK_ROOT}/data-artifacts"
COST_QUOTE_PATH="${WORK_ROOT}/current-cost-quote.json"
OUTPUT_ROOT="${WORK_ROOT}/result"

install -d -m 0700 "${WORK_ROOT}" "${ARTIFACT_ROOT}"
git clone --filter=blob:none --no-checkout \
  "${SPEECH_REPOSITORY_URL}" "${SOURCE_ROOT}"
git -C "${SOURCE_ROOT}" fetch --depth=1 origin "${SPEECH_REVISION}"
git -C "${SOURCE_ROOT}" checkout --detach "${SPEECH_REVISION}"
if [[ "$(git -C "${SOURCE_ROOT}" rev-parse HEAD)" != "${SPEECH_REVISION}" ]]; then
  echo "checked-out speech revision does not match" >&2
  exit 1
fi

export USE_TF=0
export HF_HOME="${WORK_ROOT}/huggingface"
export TOKENIZERS_PARALLELISM=false
python3 -m pip install --no-cache-dir "${SOURCE_ROOT}[lora]"

ARTIFACT_FILES=(
  run-summary.json
  provenance.private.jsonl
  train-clean.manifest.json
  train-wind_snr0.manifest.json
  dev-clean.manifest.json
  dev-wind_snr0.manifest.json
  train-clean.zip
  train-wind_snr0.zip
  dev-clean.zip
  dev-wind_snr0.zip
  train-labels.zip
  dev-labels.zip
)
for filename in "${ARTIFACT_FILES[@]}"; do
  gcloud storage cp \
    "${DATA_GCS_PREFIX}/${filename}" \
    "${ARTIFACT_ROOT}/${filename}" >/dev/null
  chmod 0600 "${ARTIFACT_ROOT}/${filename}"
done
gcloud storage cp "${COST_QUOTE_GCS_URI}" "${COST_QUOTE_PATH}" >/dev/null
chmod 0600 "${COST_QUOTE_PATH}"

timeout --signal=TERM --kill-after=60s "${TRAIN_TIMEOUT_SECONDS}" \
  env PYTHONPATH="${SOURCE_ROOT}/src" USE_TF=0 \
  python3 -m chemicheck119_speech.lora_training \
  --execution-config "${SOURCE_ROOT}/config/whisper_lora_execution_v1.json" \
  --experiment-config "${SOURCE_ROOT}/config/whisper_lora_experiment_v1.json" \
  --artifact-root "${ARTIFACT_ROOT}" \
  --cost-quote "${COST_QUOTE_PATH}" \
  --output-dir "${OUTPUT_ROOT}" \
  --confirm-bounded-experiment RUN_BOUNDED_LORA_ONCE

gcloud storage cp --recursive --if-generation-match=0 \
  "${OUTPUT_ROOT}" "${OUTPUT_GCS_PREFIX}/" >/dev/null
echo "bounded LoRA training artifact upload completed"
