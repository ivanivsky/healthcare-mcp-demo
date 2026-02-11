#!/bin/bash
# Health Advisor - GCP Setup Script
# Run this once before deploying to set up secrets and permissions

set -e

PROJECT_ID="healthcare-demo-app"
REGION="us-central1"

echo "=== Health Advisor GCP Setup ==="
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
    --description="Health Advisor MCP Demo images" 2>/dev/null || echo "  Repository already exists"

# Create secret for Anthropic API key
echo ""
echo ">>> Setting up Anthropic API key secret..."

# Check if secret exists
if gcloud secrets describe anthropic-api-key --project ${PROJECT_ID} &>/dev/null; then
    echo "  Secret 'anthropic-api-key' already exists."
    read -p "  Do you want to update it? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if [ -z "${ANTHROPIC_API_KEY}" ]; then
            if [ -f .env ]; then
                source .env
            fi
        fi
        if [ -z "${ANTHROPIC_API_KEY}" ]; then
            read -sp "  Enter your Anthropic API key: " ANTHROPIC_API_KEY
            echo
        fi
        echo -n "${ANTHROPIC_API_KEY}" | gcloud secrets versions add anthropic-api-key \
            --data-file=- \
            --project ${PROJECT_ID}
        echo "  Secret updated!"
    fi
else
    echo "  Creating new secret 'anthropic-api-key'..."
    if [ -z "${ANTHROPIC_API_KEY}" ]; then
        if [ -f .env ]; then
            source .env
        fi
    fi
    if [ -z "${ANTHROPIC_API_KEY}" ]; then
        read -sp "  Enter your Anthropic API key: " ANTHROPIC_API_KEY
        echo
    fi
    echo -n "${ANTHROPIC_API_KEY}" | gcloud secrets create anthropic-api-key \
        --data-file=- \
        --project ${PROJECT_ID}
    echo "  Secret created!"
fi

# Grant Cloud Run service account access to the secret
echo ""
echo ">>> Granting Cloud Run access to secrets..."
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding anthropic-api-key \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" \
    --project ${PROJECT_ID}

echo ""
echo "=== Setup Complete ==="
echo ""
echo "You can now deploy with: ./deploy.sh all"
