#!/usr/bin/env bash
# ShipLog installer for macOS / Linux
# Creates a virtual environment, installs dependencies, and runs the app.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "========================================="
echo "  ShipLog — Marine Project Manager"
echo "  Installer for macOS / Linux"
echo "========================================="
echo

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found. Please install Python 3.11+."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Found Python $PYTHON_VERSION"
echo

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "[1/2] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "      Done."
else
    echo "[1/2] Virtual environment already exists."
fi
echo

# Activate and install
echo "[2/2] Installing dependencies..."
echo
source "$VENV_DIR/bin/activate"
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" --trusted-host pypi.org --trusted-host files.pythonhosted.org
echo

echo "========================================="
echo "  Installation complete!"
echo "========================================="
echo
echo "Starting ShipLog now..."
echo
python3 "$SCRIPT_DIR/main.py"
