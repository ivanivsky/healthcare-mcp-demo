#!/bin/bash
# My Health Access - GCP Setup Script
# Run this once before deploying to set up secrets and permissions

set -e

PROJECT_ID="healthcare-demo-app"
REGION="us-central1"

echo "=== My Health Access GCP Setup ==="
echo "Project: ${PROJECT_ID}"
echo ""

# Enable required APIs
echo ">>> Enabling required GCP APIs..."
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    --project ${PROJECT_ID}

# Configure Docker for Artifact Registry
echo ">>> Configuring Docker authentication for Artifact Registry..."
gcloud auth configure-docker ${REGION}-docker.pkg.dev --quiet

# Create Artifact Registry repository (if not exists)
echo ">>> Creating Artifact Registry repository..."
gcloud artifacts repositories create healthcare-mcp \
    --repository-format=docker \
    --location=${REGION} \
    --project=${PROJECT_ID} \
    --description="My Health Access MCP Demo images" 2>/dev/null || echo "  Repository already exists"

# Note: Vertex AI uses Workload Identity on Cloud Run - no API key secret needed.
# The default compute service account has Vertex AI User role.

# Create secret for session (used by SessionMiddleware)
echo ""
echo ">>> Setting up Session secret..."

if gcloud secrets describe session-secret --project ${PROJECT_ID} &>/dev/null; then
    echo "  Secret 'session-secret' already exists."
else
    echo "  Creating new secret 'session-secret' with random value..."
    # Generate a random 64-character hex string for session secret
    SESSION_SECRET=$(openssl rand -hex 32)
    echo -n "${SESSION_SECRET}" | gcloud secrets create session-secret \
        --data-file=- \
        --project ${PROJECT_ID}
    echo "  Secret created!"
fi

# Grant Cloud Run service account access to secrets
echo ""
echo ">>> Granting Cloud Run access to secrets..."
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')

gcloud secrets add-iam-policy-binding session-secret \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" \
    --project ${PROJECT_ID}

echo ""
echo "=== Setup Complete ==="
echo ""
echo "You can now deploy with: ./deploy.sh all"
