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

echo "Infrastructure configuration checks passed."
