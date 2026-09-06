#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIRECTORY="$(cd "${SCRIPT_DIRECTORY}/.." && pwd)"

for script in "${SCRIPT_DIRECTORY}"/*.sh; do
  bash -n "${script}"
done

DEPLOY_SCRIPT="${SCRIPT_DIRECTORY}/deploy_speech_eval_job.sh"
grep -q -- '--tasks=1' "${DEPLOY_SCRIPT}"
grep -q -- '--parallelism=1' "${DEPLOY_SCRIPT}"
grep -q -- '--max-retries=0' "${DEPLOY_SCRIPT}"
grep -q -- '--task-timeout=2h' "${DEPLOY_SCRIPT}"
grep -q -- '--local-files-only' "${DEPLOY_SCRIPT}"
grep -q 'roles/storage.objectViewer' "${SCRIPT_DIRECTORY}/setup_gcp_ml.sh"
grep -q 'roles/storage.objectCreator' "${SCRIPT_DIRECTORY}/setup_gcp_ml.sh"
grep -q -- '--preferred-value=1' "${SCRIPT_DIRECTORY}/request_cloud_run_l4_quota.sh"
grep -q '"age": 30' "${INFRA_DIRECTORY}/config/ml-bucket-lifecycle.json"
grep -q '"age": 90' "${INFRA_DIRECTORY}/config/ml-bucket-lifecycle.json"

LORA_RUNNER="${SCRIPT_DIRECTORY}/run_whisper_lora_once.sh"
LORA_STARTUP="${SCRIPT_DIRECTORY}/startup_whisper_lora.sh"
grep -q -- '--accelerator="type=${LORA_GPU_TYPE},count=1"' "${LORA_RUNNER}"
grep -q -- '--provisioning-model=STANDARD' "${LORA_RUNNER}"
grep -q -- '--no-restart-on-failure' "${LORA_RUNNER}"
grep -q -- '--max-run-duration="${LORA_MAX_RUN_SECONDS}s"' "${LORA_RUNNER}"
grep -q -- '--instance-termination-action=DELETE' "${LORA_RUNNER}"
grep -q -- '--boot-disk-auto-delete' "${LORA_RUNNER}"
grep -q 'trap cleanup_instance EXIT INT TERM' "${LORA_RUNNER}"
grep -q 'RUN_BOUNDED_LORA_ONCE' "${LORA_STARTUP}"
grep -q 'timeout --signal=TERM --kill-after=60s' "${LORA_STARTUP}"
grep -q -- '--if-generation-match=0' "${LORA_STARTUP}"
grep -q 'validate_lora_cost_quote.py' "${LORA_RUNNER}"
grep -q 'AUTHORIZATION_CLAIM_GCS_URI' "${LORA_RUNNER}"
grep -q -- '--authorization-claim' "${LORA_STARTUP}"
grep -q -- '--runner-revision' "${LORA_STARTUP}"
grep -q -- '--if-generation-match=0' "${LORA_RUNNER}"
python3 -m py_compile "${SCRIPT_DIRECTORY}/validate_lora_cost_quote.py"
grep -q '^LORA_MAX_RUN_SECONDS=10800$' "${INFRA_DIRECTORY}/config/ml.env"
grep -q '^LORA_TRAIN_TIMEOUT_SECONDS=9900$' "${INFRA_DIRECTORY}/config/ml.env"

echo "Infrastructure configuration checks passed."
