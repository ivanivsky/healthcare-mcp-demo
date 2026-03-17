#!/bin/bash
# Start the MCP Server

cd "$(dirname "$0")/.."

echo "Starting MCP Server..."
python mcp_server/server.py
