#!/bin/bash
# Script to run MCP Inspector

# Check if npx is installed
if ! command -v npx &> /dev/null; then
    echo "Error: npx is not installed. Please install Node.js."
    exit 1
fi

echo "Starting MCP Inspector for support-case-mcp..."
echo "Ensure you have the Python dependencies installed (pip install .)"

# Use uv run if available, else python
if command -v uv &> /dev/null; then
    npx @modelcontextprotocol/inspector uv run support-case-mcp/server.py
else
    # Assuming python is in path and env is set
    npx @modelcontextprotocol/inspector python support-case-mcp/server.py
fi
