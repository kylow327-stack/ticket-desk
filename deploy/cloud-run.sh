#!/usr/bin/env bash
# Deploy notes as a script. Do not run this from the agent box in this build.
# Target: your Google Cloud project, region asia-east2 (Hong Kong), scale-to-zero.
# The API key must come from Secret Manager, never from the container image.
set -euo pipefail

PROJECT="${PROJECT:?Set PROJECT to your Google Cloud project id}"
REGION="${REGION:-asia-east2}"
SERVICE="${SERVICE:-ticket-guardrail-agent}"
SECRET_NAME="${SECRET_NAME:-DEEPSEEK_API_KEY}"

echo "This script is a checklist. Review every command before running it."
echo "Project=${PROJECT} Region=${REGION} Service=${SERVICE}"
echo
echo "1) Create or update the secret (paste the key at the prompt, do not echo it):"
echo "   printf '%s' \"\$DEEPSEEK_API_KEY\" | gcloud secrets create ${SECRET_NAME} --data-file=- --project=${PROJECT}"
echo "   # or: gcloud secrets versions add ${SECRET_NAME} --data-file=- --project=${PROJECT}"
echo
echo "2) Enable APIs if they are not already on (the project must have billing linked):"
echo "   gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com cloudbuild.googleapis.com --project=${PROJECT}"
echo
echo "3) Deploy from source, scale to zero, inject the secret:"
echo "   gcloud run deploy ${SERVICE} \\"
echo "     --source . \\"
echo "     --project ${PROJECT} \\"
echo "     --region ${REGION} \\"
echo "     --memory 512Mi \\"
echo "     --cpu 1 \\"
echo "     --min-instances 0 \\"
echo "     --max-instances 2 \\"
echo "     --concurrency 8 \\"
echo "     --no-allow-unauthenticated \\"
echo "     --set-secrets=DEEPSEEK_API_KEY=${SECRET_NAME}:latest \\"
echo "     --set-env-vars=DEEPSEEK_MODEL=deepseek-flash,DEEPSEEK_BASE_URL=https://api.deepseek.com"
echo
echo "Optional LangSmith traces (not required, not deployed by this script):"
echo "   Create a LANGSMITH_API_KEY secret the same way as DEEPSEEK_API_KEY, then add:"
echo "     --set-secrets=DEEPSEEK_API_KEY=${SECRET_NAME}:latest,LANGSMITH_API_KEY=LANGSMITH_API_KEY:latest \\"
echo "     --set-env-vars=DEEPSEEK_MODEL=deepseek-flash,DEEPSEEK_BASE_URL=https://api.deepseek.com,LANGSMITH_TRACING=true,LANGSMITH_PROJECT=ticket-desk,LANGSMITH_WORKSPACE_ID=<workspace-id>"
echo "   Organisation-scoped service keys need LANGSMITH_WORKSPACE_ID. Leave LangSmith off the"
echo "   live walkthrough until you explicitly want unauthenticated visitors writing traces."
echo
echo "If Cloud Run rejects region ${REGION}, fall back to a small e2-small VM in asia-east2."
echo "Do not leave that VM running after the demo. See deploy/teardown.sh and the README."
