#!/bin/bash
# My Health Access - Cloud Run Deployment Script
# Usage: ./deploy.sh [mcp|backend|all|status]

set -e

# Configuration
PROJECT_ID="healthcare-demo-app"
REGION="us-central1"
REGISTRY="us-central1-docker.pkg.dev/${PROJECT_ID}/healthcare-mcp"

# Service names
MCP_SERVICE="my-health-access-mcp"
BACKEND_SERVICE="my-health-access-backend"

# Image names
MCP_IMAGE="${REGISTRY}/mcp-server:latest"
BACKEND_IMAGE="${REGISTRY}/backend:latest"

echo "=== My Health Access Cloud Run Deployment ==="
echo "Project: ${PROJECT_ID}"
echo "Region: ${REGION}"
echo ""

# Function to deploy MCP server
deploy_mcp() {
    echo ">>> Building MCP server image (linux/amd64 for Cloud Run)..."
    docker build --platform linux/amd64 -t ${MCP_IMAGE} -f Dockerfile.mcp-server .

    echo ">>> Pushing MCP server image to Artifact Registry..."
    docker push ${MCP_IMAGE}

    echo ">>> Deploying MCP server to Cloud Run..."
    gcloud run deploy ${MCP_SERVICE} \
        --image ${MCP_IMAGE} \
        --region ${REGION} \
        --project ${PROJECT_ID} \
        --platform managed \
        --allow-unauthenticated \
        --port 8001 \
        --memory 512Mi \
        --cpu 1 \
        --min-instances 0 \
        --max-instances 3 \
        --add-cloudsql-instances ${PROJECT_ID}:${REGION}:healthcare-demo-db \
        --set-env-vars "MCP_HOST=0.0.0.0,MCP_PORT=8001,MCP_TRANSPORT=sse,LOG_LEVEL=INFO" \
        --set-secrets "MCP_INTERNAL_TOKEN=mcp-internal-token:latest,MCP_JWT_SECRET=mcp-jwt-secret:latest,DB_CONNECTION_STRING=db-connection-string:latest"

    # Get the MCP server URL
    MCP_URL=$(gcloud run services describe ${MCP_SERVICE} \
        --region ${REGION} \
        --project ${PROJECT_ID} \
        --format 'value(status.url)')

    echo ""
    echo ">>> MCP Server deployed!"
    echo "    URL: ${MCP_URL}"
    echo "    SSE Endpoint: ${MCP_URL}/sse"
    echo ""

    # Export for backend deployment
    export MCP_SERVER_URL="${MCP_URL}/sse"
}

# Function to deploy backend
deploy_backend() {
    # Get MCP server URL if not already set
    if [ -z "${MCP_SERVER_URL}" ]; then
        echo ">>> Getting MCP server URL..."
        MCP_URL=$(gcloud run services describe ${MCP_SERVICE} \
            --region ${REGION} \
            --project ${PROJECT_ID} \
            --format 'value(status.url)' 2>/dev/null || echo "")

        if [ -z "${MCP_URL}" ]; then
            echo "ERROR: MCP server not deployed yet. Deploy it first with: ./deploy.sh mcp"
            exit 1
        fi
        MCP_SERVER_URL="${MCP_URL}/sse"
    fi

    echo ">>> Building backend image (linux/amd64 for Cloud Run)..."
    docker build --platform linux/amd64 -t ${BACKEND_IMAGE} -f Dockerfile.backend .

    echo ">>> Pushing backend image to Artifact Registry..."
    docker push ${BACKEND_IMAGE}

    echo ">>> Deploying backend to Cloud Run..."
    echo "    MCP Server URL: ${MCP_SERVER_URL}"

    gcloud run deploy ${BACKEND_SERVICE} \
        --image ${BACKEND_IMAGE} \
        --region ${REGION} \
        --project ${PROJECT_ID} \
        --platform managed \
        --allow-unauthenticated \
        --port 8080 \
        --memory 1Gi \
        --cpu 1 \
        --min-instances 0 \
        --max-instances 5 \
        --add-cloudsql-instances ${PROJECT_ID}:${REGION}:healthcare-demo-db \
        --set-env-vars "BACKEND_HOST=0.0.0.0,BACKEND_PORT=8080,MCP_SERVER_URL=${MCP_SERVER_URL},HTTPS_ONLY=true,DEBUG=false,LOG_LEVEL=INFO,FIREBASE_API_KEY=AIzaSyC9IgURwS2nCxnou6QwW--x07DRTaG63ZY,FIREBASE_AUTH_DOMAIN=healthcare-demo-app.firebaseapp.com,GOOGLE_CLOUD_PROJECT=healthcare-demo-app,GOOGLE_CLOUD_LOCATION=us-central1" \
        --set-env-vars "^|^CORS_ORIGINS=https://ai-wtf.xyz,https://www.ai-wtf.xyz,https://my-health-access-backend-834239374191.us-central1.run.app" \
        --set-secrets "FIREBASE_PROJECT_ID=firebase-project-id:latest,MCP_INTERNAL_TOKEN=mcp-internal-token:latest,MCP_JWT_SECRET=mcp-jwt-secret:latest,DB_CONNECTION_STRING=db-connection-string:latest,MODEL_ARMOR_TEMPLATE=model-armor-template:latest"

    # Get the backend URL
    BACKEND_URL=$(gcloud run services describe ${BACKEND_SERVICE} \
        --region ${REGION} \
        --project ${PROJECT_ID} \
        --format 'value(status.url)')

    echo ""
    echo ">>> Backend deployed!"
    echo "    URL: ${BACKEND_URL}"
    echo ""
}

# Function to show status
show_status() {
    echo ">>> Current deployment status:"
    echo ""

    echo "MCP Server:"
    gcloud run services describe ${MCP_SERVICE} \
        --region ${REGION} \
        --project ${PROJECT_ID} \
        --format 'table(status.url, status.conditions[0].status)' 2>/dev/null || echo "  Not deployed"
    echo ""

    echo "Backend:"
    gcloud run services describe ${BACKEND_SERVICE} \
        --region ${REGION} \
        --project ${PROJECT_ID} \
        --format 'table(status.url, status.conditions[0].status)' 2>/dev/null || echo "  Not deployed"
    echo ""
}

# Main
case "${1:-all}" in
    mcp)
        deploy_mcp
        ;;
    backend)
        deploy_backend
        ;;
    all)
        deploy_mcp
        deploy_backend
        echo ""
        echo "=== Deployment Complete ==="
        show_status
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 [mcp|backend|all|status]"
        echo ""
        echo "  mcp     - Deploy only the MCP server"
        echo "  backend - Deploy only the backend (requires MCP server)"
        echo "  all     - Deploy both services (default)"
        echo "  status  - Show deployment status"
        exit 1
        ;;
esac
