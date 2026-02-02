#!/bin/bash
# Start the MCP Server

cd "$(dirname "$0")/.."

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "Warning: ANTHROPIC_API_KEY is not set"
fi

echo "Starting MCP Server..."
python mcp_server/server.py
