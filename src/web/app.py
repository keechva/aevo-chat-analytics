"""
Main FastAPI application entry point.
Serves the web UI and REST API for AEVO Product Discovery & Chat Analytics.
"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from src.web.api import api_router

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "src" / "web" / "static"

app = FastAPI(
    title="AEVO Chat-analytics: Product Discovery",
    description="Платформа выявления продуктовых гипотез и болей экспатов в Таиланде",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# Mount static files
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
@app.head("/")
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "AEVO Chat-analytics API is running. index.html not found yet."}

@app.get("/standalone")
def serve_standalone():
    standalone_file = BASE_DIR / "aevo_insights_dashboard.html"
    if standalone_file.exists():
        return FileResponse(standalone_file)
    return {"error": "Standalone report not found"}

@app.get("/download-report")
def download_standalone():
    standalone_file = BASE_DIR / "aevo_insights_dashboard.html"
    if standalone_file.exists():
        return FileResponse(
            standalone_file,
            filename="aevo_insights_dashboard.html",
            media_type="text/html"
        )
    return {"error": "Standalone report not found"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.web.app:app", host="0.0.0.0", port=8000, reload=True)
