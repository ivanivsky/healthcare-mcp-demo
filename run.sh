#!/bin/bash
# Local development runner for My Health Access
# Loads .env file if present and starts the backend

set -e

# Load .env file if it exists
if [ -f .env ]; then
    echo "Loading environment from .env..."
    set -a
    source .env
    set +a
fi

# Check required vars
missing=""
[ -z "$GOOGLE_CLOUD_PROJECT" ] && missing="$missing GOOGLE_CLOUD_PROJECT"
[ -z "$FIREBASE_API_KEY" ] && missing="$missing FIREBASE_API_KEY"
[ -z "$FIREBASE_AUTH_DOMAIN" ] && missing="$missing FIREBASE_AUTH_DOMAIN"

if [ -n "$missing" ]; then
    echo "ERROR: Missing required environment variables:$missing"
    echo ""
    echo "Option 1: Create a .env file:"
    echo "  cp .env.example .env"
    echo "  # Edit .env with your actual values"
    echo ""
    echo "Option 2: Export directly:"
    echo "  export GOOGLE_CLOUD_PROJECT='healthcare-demo-app'"
    echo "  export FIREBASE_API_KEY='AIza...'"
    echo "  export FIREBASE_AUTH_DOMAIN='your-project.firebaseapp.com'"
    exit 1
fi

echo "Starting backend on http://localhost:8080"
echo "Firebase config: authDomain=$FIREBASE_AUTH_DOMAIN"
python -m backend.main
