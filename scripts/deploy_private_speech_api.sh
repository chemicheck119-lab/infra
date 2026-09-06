#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# != 1 )); then
  echo "usage: $0 SPEECH_IMAGE_DIGEST_URI" >&2
  exit 2
fi

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_command python3
require_project

IMAGE_DIGEST_URI="$1"
EXPECTED_PREFIX="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/${SPEECH_API_IMAGE_NAME}@sha256:"
if [[ "${IMAGE_DIGEST_URI}" != "${EXPECTED_PREFIX}"* ]] || \
  [[ ! "${IMAGE_DIGEST_URI##*@sha256:}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Speech API image must be an immutable digest in the configured repository" >&2
  exit 1
fi
gcloud artifacts docker images describe "${IMAGE_DIGEST_URI}" \
  --project="${PROJECT_ID}" >/dev/null
gcloud iam service-accounts describe "${SPEECH_API_RUNTIME_SERVICE_ACCOUNT}" \
  --project="${PROJECT_ID}" >/dev/null
gcloud iam service-accounts describe "${BACKEND_RUNTIME_SERVICE_ACCOUNT}" \
  --project="${PROJECT_ID}" >/dev/null
gcloud secrets versions describe latest \
  --secret="${SPEECH_API_KEY_SECRET}" \
  --project="${PROJECT_ID}" >/dev/null

# ingress=all은 VPC connector가 없는 현재 Backend가 run.app URL로 호출하기 위한 경계다.
# 공개 접근은 IAM에서 별도로 차단하고 Backend runtime service account만 invoker로 둔다.
# request concurrency는 짧은 burst를 앱의 1초 queue gate까지 전달한다. 실제 모델 추론은
# speech-service 내부 semaphore 1로 계속 제한된다.
gcloud run deploy "${SPEECH_API_SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --platform=managed \
  --image="${IMAGE_DIGEST_URI}" \
  --service-account="${SPEECH_API_RUNTIME_SERVICE_ACCOUNT}" \
  --port=8080 \
  --cpu=4 \
  --memory=8Gi \
  --concurrency="${SPEECH_API_CONTAINER_CONCURRENCY}" \
  --min-instances=0 \
  --max-instances=1 \
  --timeout=60s \
  --cpu-boost \
  --cpu-throttling \
  --ingress=all \
  --no-allow-unauthenticated \
  --set-env-vars="CHEMICHECK119_SPEECH_MODEL=small,CHEMICHECK119_SPEECH_DEVICE=cpu,CHEMICHECK119_SPEECH_COMPUTE_TYPE=int8,CHEMICHECK119_SPEECH_CPU_THREADS=4,CHEMICHECK119_SPEECH_LOCAL_FILES_ONLY=true,CHEMICHECK119_SPEECH_ALLOW_ANONYMOUS=false" \
  --set-secrets="CHEMICHECK119_SPEECH_API_KEY=${SPEECH_API_KEY_SECRET}:latest"

gcloud run services add-iam-policy-binding "${SPEECH_API_SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:${BACKEND_RUNTIME_SERVICE_ACCOUNT}" \
  --role="roles/run.invoker" >/dev/null

IAM_POLICY="$({
  gcloud run services get-iam-policy "${SPEECH_API_SERVICE_NAME}" \
    --project="${PROJECT_ID}" --region="${REGION}" --format=json
})"
python3 -c '
import json
import sys
expected = "serviceAccount:" + sys.argv[1]
policy = json.load(sys.stdin)
invokers = {
    member
    for binding in policy.get("bindings", [])
    if binding.get("role") == "roles/run.invoker"
    for member in binding.get("members", [])
}
if invokers != {expected}:
    raise SystemExit("Speech API invokers must contain only the Backend runtime service account")
' "${BACKEND_RUNTIME_SERVICE_ACCOUNT}" <<<"${IAM_POLICY}"

gcloud run services describe "${SPEECH_API_SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(status.url)'
