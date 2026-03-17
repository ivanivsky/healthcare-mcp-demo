#!/bin/bash
# Start the FastAPI Backend

cd "$(dirname "$0")/.."

# Note: Uses Google Cloud Application Default Credentials (ADC).
# Run: gcloud auth application-default login

echo "Starting Backend API on http://localhost:8080..."
python -m backend.main
