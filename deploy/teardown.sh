#!/usr/bin/env bash
# Tear down the Cloud Run service and (optionally) the secret. Do not run during this build.
set -euo pipefail

PROJECT="${PROJECT:?Set PROJECT to your Google Cloud project id}"
REGION="${REGION:-asia-east2}"
SERVICE="${SERVICE:-ticket-guardrail-agent}"
SECRET_NAME="${SECRET_NAME:-DEEPSEEK_API_KEY}"

echo "Delete the Cloud Run service:"
echo "  gcloud run services delete ${SERVICE} --region ${REGION} --project ${PROJECT} --quiet"
echo
echo "Optional — delete the secret (only if nothing else uses it):"
echo "  gcloud secrets delete ${SECRET_NAME} --project ${PROJECT} --quiet"
echo
echo "If you used a VM fallback instead of Cloud Run:"
echo "  gcloud compute instances stop ticket-guardrail-demo --zone asia-east2-a --project ${PROJECT}"
echo "  gcloud compute instances delete ticket-guardrail-demo --zone asia-east2-a --project ${PROJECT} --quiet"
echo "  gcloud compute firewall-rules delete ticket-guardrail-demo-http --project ${PROJECT} --quiet"
