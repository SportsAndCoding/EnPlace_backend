"""
modules/nightly_pipeline

Nightly data pipeline for En Place.

Components:
- demo_bistro_seeder: Generates daily check-ins with intentional patterns
- run_nightly_pipeline: Main orchestrator

Usage:
    python -m modules.nightly_pipeline.run_nightly_pipeline
"""

from .demo_bistro_seeder import (
    generate_demo_bistro_checkins,
    seed_demo_bistro_history,
    seed_today,
    DEMO_BISTRO_PATTERNS,
    StaffPattern,
    seed_demo_shifts,
    ensure_critical_gaps,
)

from .run_nightly_pipeline import run_pipeline

__all__ = [
    "run_pipeline",
    "generate_demo_bistro_checkins",
    "seed_demo_bistro_history",
    "seed_today",
    "DEMO_BISTRO_PATTERNS",
    "StaffPattern",
    "seed_demo_shifts",
    "ensure_critical_gaps",
]