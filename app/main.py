"""
Home Memory - FastAPI Application

Main entry point for the Home Memory API.
This is a THIN ADAPTER layer - no business logic here.

All mutations go through operations.py.
All path resolution goes through resolver.py.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.engine import create_tables
from app.api.containers import router as containers_router
from app.api.items import router as items_router
from app.api.images import router as images_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.
    
    Creates database tables on startup.
    """
    # Startup: create tables
    create_tables()
    yield
    # Shutdown: cleanup (if needed)


# Create FastAPI app
app = FastAPI(
    title="Home Memory API",
    description=(
        "A local-first system for remembering where physical objects "
        "are stored in your home. Voice-first, vision-assisted."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routers
app.include_router(containers_router)
app.include_router(items_router)
app.include_router(images_router)


# -----------------------------------------------------------------------------
# Health Check
# -----------------------------------------------------------------------------

@app.get("/health", tags=["health"])
def health_check():
    """
    Health check endpoint.
    
    Returns OK if the API is running.
    """
    return {"status": "ok", "service": "home-memory"}


@app.get("/", tags=["health"])
def root():
    """
    Root endpoint with API info.
    """
    return {
        "name": "Home Memory API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }

