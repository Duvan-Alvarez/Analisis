#!/usr/bin/env bash

# Azure App Service startup script for FastAPI + Uvicorn
python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
