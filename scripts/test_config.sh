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
if grep -q -- '--accelerator=' "${LORA_RUNNER}"; then
  echo "G2 already includes its L4 accelerator" >&2
  exit 1
fi
grep -q '^LORA_MACHINE_TYPE=g2-standard-4$' "${INFRA_DIRECTORY}/config/ml.env"
grep -q '^LORA_GPU_TYPE=nvidia-l4$' "${INFRA_DIRECTORY}/config/ml.env"
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
grep -q 'LORA_ZONE.*LORA_REGION' "${LORA_RUNNER}"
grep -q -- '--authorization-claim' "${LORA_STARTUP}"
grep -q -- '--runner-revision' "${LORA_STARTUP}"
grep -q -- '--if-generation-match=0' "${LORA_RUNNER}"
python3 -m py_compile "${SCRIPT_DIRECTORY}/validate_lora_cost_quote.py"
python3 -m py_compile "${SCRIPT_DIRECTORY}/audit_artifact_cleanup.py"
python3 -m py_compile "${SCRIPT_DIRECTORY}/audit_private_speech_api.py"
python3 "${SCRIPT_DIRECTORY}/audit_artifact_cleanup.py" --self-test
python3 "${SCRIPT_DIRECTORY}/audit_private_speech_api.py" --self-test
if grep -Eq '"(delete|update|set-cleanup-policies|remove-tags)"' \
  "${SCRIPT_DIRECTORY}/audit_artifact_cleanup.py"; then
  echo "artifact cleanup auditor must remain read-only" >&2
  exit 1
fi
grep -q '^LORA_MAX_RUN_SECONDS=10800$' "${INFRA_DIRECTORY}/config/ml.env"
grep -q '^LORA_TRAIN_TIMEOUT_SECONDS=9900$' "${INFRA_DIRECTORY}/config/ml.env"
grep -q '^LORA_REGION=asia-northeast3$' "${INFRA_DIRECTORY}/config/ml.env"
grep -q '^LORA_ZONE=asia-northeast3-a$' "${INFRA_DIRECTORY}/config/ml.env"

SPEECH_API_BUILD="${SCRIPT_DIRECTORY}/build_speech_api_image.sh"
SPEECH_API_SETUP="${SCRIPT_DIRECTORY}/setup_speech_api_identity.sh"
SPEECH_API_DEPLOY="${SCRIPT_DIRECTORY}/deploy_private_speech_api.sh"
SPEECH_API_AUDIT="${SCRIPT_DIRECTORY}/audit_private_speech_api.py"
grep -q -- '--file=Dockerfile.api' "${INFRA_DIRECTORY}/config/cloudbuild-speech-api.yaml"
grep -q -- '--build-arg=EMBED_WHISPER_MODEL=true' "${INFRA_DIRECTORY}/config/cloudbuild-speech-api.yaml"
grep -q 'currently advertised main commit' "${SPEECH_API_BUILD}"
grep -q 'refusing to overwrite an existing immutable commit tag' "${SPEECH_API_BUILD}"
grep -q 'refusing to replace or rotate an existing Speech API secret' "${SPEECH_API_SETUP}"
grep -q 'single-line printable ASCII without whitespace' "${SPEECH_API_SETUP}"
grep -q '^SPEECH_API_CONTAINER_CONCURRENCY=4$' "${INFRA_DIRECTORY}/config/ml.env"
grep -q -- '--concurrency="${SPEECH_API_CONTAINER_CONCURRENCY}"' "${SPEECH_API_DEPLOY}"
grep -q -- '--min-instances=0' "${SPEECH_API_DEPLOY}"
grep -q -- '--max-instances=1' "${SPEECH_API_DEPLOY}"
grep -q -- '--no-allow-unauthenticated' "${SPEECH_API_DEPLOY}"
grep -q 'CHEMICHECK119_SPEECH_LOCAL_FILES_ONLY=true' "${SPEECH_API_DEPLOY}"
grep -q 'CHEMICHECK119_SPEECH_ALLOW_ANONYMOUS=false' "${SPEECH_API_DEPLOY}"
grep -q 'invokers must contain only the Backend runtime service account' "${SPEECH_API_DEPLOY}"
grep -q '실제 WAV' "${SPEECH_API_AUDIT}"

echo "Infrastructure configuration checks passed."
