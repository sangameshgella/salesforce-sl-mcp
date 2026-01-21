#!/bin/bash
# Script to run MCP Inspector

# Check if npx is installed
if ! command -v npx &> /dev/null; then
    echo "Error: npx is not installed. Please install Node.js."
    exit 1
fi

echo "Starting MCP Inspector for support-case-mcp..."
echo "Ensure you have the Python dependencies installed (pip install .)"

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Change to the script directory so uv finds pyproject.toml and sets up the environment
cd "$SCRIPT_DIR" || exit

echo "Running server from: $(pwd)"

# Use uv run if available, else python
if command -v uv &> /dev/null; then
    # uv run will automatically install/sync dependencies from pyproject.toml
    npx @modelcontextprotocol/inspector uv run server.py
else
    # Assuming python is in path and env is set
    npx @modelcontextprotocol/inspector python server.py
fi
