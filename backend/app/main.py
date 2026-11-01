import os
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env", override=False)

from app.api.assistant import router as assistant_router
from app.api.admin_dataset import router as admin_dataset_router
from app.api.auth import router as auth_router
from app.api.bookings import router as bookings_router
from app.api.crisis import router as crisis_router
from app.api.exams import router as exams_router
from app.api.spectra_events import router as spectra_events_router
from app.api.timetable import router as timetable_router
from app.api.workflows import router as workflows_router
from app.auth import AuthenticatedPrincipal, require_roles
from app.services.dataset_loader import get_dataset_summary

# Initialize CIT Nexus API
app = FastAPI(
    title="CIT Nexus API",
    description="AI-Powered Campus Scheduling and Venue Management System",
    version="1.0.0"
)

# Connect React frontend with Python backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CIT_NEXUS_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(admin_dataset_router)
app.include_router(assistant_router)
app.include_router(bookings_router)
app.include_router(crisis_router)
app.include_router(exams_router)
app.include_router(spectra_events_router)
app.include_router(timetable_router)
app.include_router(workflows_router)

# Homepage endpoint
@app.get("/")
def home():
    return {
        "project": "CIT Nexus",
        "status": "running",
        "message": "Welcome to CIT Nexus Campus Intelligence"
    }

# Backend health check
@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "backend": "FastAPI",
        "project": "CIT Nexus",
        "scheduler": "OR-Tools installed"
    }

# Project information
@app.get("/api/info")
def project_info():
    return {
        "name": "CIT Nexus",
        "tagline": "Think. Schedule. Adapt.",
        "modules": [
            "Smart Timetable",
            "Campus Crisis Mode",
            "What-If Simulator",
            "Venue Booking",
            "AI Assistant",
            "Campus Analytics"
        ]
    }
@app.get("/api/dataset/summary")
def dataset_summary(
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    del principal
    try:
        return {
            "status": "success",
            "data": get_dataset_summary()
        }
    except Exception as error:
        return {
            "status": "error",
            "message": str(error)
        }
