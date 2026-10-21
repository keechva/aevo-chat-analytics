#!/usr/bin/env python3
"""
Launcher script for AEVO Chat-analytics Web Application.
Starts the FastAPI server on http://localhost:8000.
"""

import sys
import os
from pathlib import Path
import uvicorn

BASE_DIR = Path(__file__).resolve().parent

def main():
    print("=" * 65)
    print("   AEVO Chat-analytics: Product Discovery & Pain Platform")
    print("=" * 65)
    print(f"Base Directory: {BASE_DIR}")
    print("Database: data/processed/analytics.duckdb")
    print("Starting Web Server at http://localhost:8000 ...")
    print("=" * 65)

    uvicorn.run(
        "src.web.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )

if __name__ == "__main__":
    main()
