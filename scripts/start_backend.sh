#!/bin/bash
# Start the FastAPI Backend

cd "$(dirname "$0")/.."

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "Error: ANTHROPIC_API_KEY is not set"
    echo "Please set it with: export ANTHROPIC_API_KEY='your-key-here'"
    exit 1
fi

echo "Starting Backend API on http://localhost:8080..."
python -m backend.main
