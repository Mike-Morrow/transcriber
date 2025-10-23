#!/bin/bash

# Transcription Editor - Virtual Environment Setup Script

set -e  # Exit on error

echo "🔧 Setting up virtual environment for Transcription Editor..."

# Check Python version
PYTHON_CMD=""
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
    PYTHON_CMD="python"
else
    echo "❌ Error: Python not found. Please install Python 3.10 or higher."
    exit 1
fi

echo "✓ Found Python $PYTHON_VERSION"

# Create virtual environment
if [ -d "venv" ]; then
    echo "⚠️  Virtual environment already exists. Removing old venv..."
    rm -rf venv
fi

echo "📦 Creating virtual environment..."
$PYTHON_CMD -m venv venv

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "📥 Installing dependencies from requirements.txt..."
pip install -r requirements.txt

echo ""
echo "✅ Virtual environment setup complete!"
echo ""
echo "To activate the virtual environment, run:"
echo "  source venv/bin/activate"
echo ""
echo "To run the app:"
echo "  python app/main.py"
echo ""
echo "To deactivate when you're done:"
echo "  deactivate"
echo ""
