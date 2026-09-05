#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIRECTORY}/common.sh"

require_command gcloud
require_project

gcloud services enable \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  run.googleapis.com \
  storage.googleapis.com \
  --project="${PROJECT_ID}"

if ! gcloud storage buckets describe "gs://${ML_BUCKET}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${ML_BUCKET}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention \
    --soft-delete-duration=0s
fi

gcloud storage buckets update "gs://${ML_BUCKET}" \
  --project="${PROJECT_ID}" \
  --uniform-bucket-level-access \
  --public-access-prevention \
  --lifecycle-file="${INFRA_DIRECTORY}/config/ml-bucket-lifecycle.json"

if ! gcloud iam service-accounts describe "${ML_RUNNER_SERVICE_ACCOUNT}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create chemicheck119-ml-runner \
    --project="${PROJECT_ID}" \
    --display-name="ChemiCheck119 bounded ML job runner"
fi

gcloud storage buckets add-iam-policy-binding "gs://${ML_BUCKET}" \
  --member="serviceAccount:${ML_RUNNER_SERVICE_ACCOUNT}" \
  --role="roles/storage.objectUser" \
  --project="${PROJECT_ID}" >/dev/null

echo "ML bucket and least-privilege runner are ready."
