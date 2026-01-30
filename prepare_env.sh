#!/bin/bash

# This script creates a conda environment for the server and installs the necessary packages
# The packages will not be version locked
# You should try creating the environment using environment.yml first

# This script assumes you have conda installed
# You can use mamba instead of conda if available

# Stop the script if any command fails
set -e

echo "--- Starting Environment Setup ---"

echo "Creating Conda environment: 'server' with Python 3.12..."
conda create --name server python=3.12 -y

# To allow 'conda activate' to work inside the script
source $(conda info --base)/etc/profile.d/conda.sh
conda activate server

echo "Installing FastAPI, Flask, and networking tools..."
conda install -c conda-forge requests python-dotenv fastapi uvicorn flask -y

echo "Installing FFmpeg and audio processing tools..."
conda install -c conda-forge ffmpeg pydub -y

echo "Installing PyTorch and ONNX Runtime..."
conda install -c conda-forge pytorch onnxruntime torchaudio -y

echo "Installing Data Science and Machine Learning tools..."
conda install -c conda-forge numpy pandas joblib scikit-learn lightgbm -y

# Rule of thumb: Always run pip AFTER conda to avoid dependency conflicts
echo "Installing whisper-timestamped via pip..."
pip install whisper-timestamped

echo "--- Setup Complete! Run 'conda activate server' to begin. ---"