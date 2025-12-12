"""
modules/synthetic/restaurant_simulation_runner.py

Restaurant-level orchestration for the En Place synthetic staffing simulation.
Creates a deterministic cohort of staff, runs each through their full lifecycle,
and returns three clean, flattened tables.

UPDATED: Output matches organic check-in schema:
- mood_emoji: 1-5 integer
- felt_safe, felt_fair, felt_respected: booleans
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Any

from modules.synthetic.staff_simulation_runner import simulate_staff_lifecycle
from modules.synthetic.personas import PERSONA_DEFINITIONS


def _deterministic_staff_id(restaurant_id: int, index: int) -> str:
    """Generate a stable staff_id from restaurant_id and staff index."""
    key = f"{restaurant_id}:{index}"
    return hashlib.sha1(key.encode()).hexdigest()


def _choose_persona_deterministically(
    weights: Dict[str, float],
    restaurant_id: int,
    staff_index: int,
) -> str:
    """Select a starting persona using fully deterministic weighted choice."""
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"persona_weights must sum to ~1.0, got {total:.6f}")

    # Stable seed using SHA-1
    seed_key = f"{restaurant_id}:{staff_index}:persona_seed"
    seed_hash = hashlib.sha1(seed_key.encode()).hexdigest()
    seed_int = int(seed_hash, 16)
    offset = (seed_int % 1_000_000_000) / 1_000_000_000.0

    cumulative = 0.0
    for persona, weight in weights.items():
        if persona not in PERSONA_DEFINITIONS:
            raise ValueError(f"Unknown persona '{persona}' in persona_weights")
        cumulative += weight / total
        if offset < cumulative:
            return persona

    # Fallback (numerical safety)
    return next(iter(PERSONA_DEFINITIONS))


def simulate_restaurant(
    restaurant_id: int,
    number_of_staff: int,
    simulation_days: int,
    persona_weights: Dict[str, float],
    restaurant_profile: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Simulate an entire restaurant's staffing history.

    Parameters
    ----------
    restaurant_id : int
        Unique identifier for the restaurant.
    number_of_staff : int
        Number of synthetic employees to generate.
    simulation_days : int
        Length of the simulation in days.
    persona_weights : Dict[str, float]
        Weighted distribution of starting personas.
        Keys must exactly match keys in PERSONA_DEFINITIONS.
    restaurant_profile : Dict[str, Any]
        Restaurant configuration affecting behavior patterns.

    Returns
    -------
    dict
        Three tables:
            "staff_master"     -> one row per employee
            "daily_emotions"   -> one row per employee per simulated day
            "daily_behavior"   -> one row per employee per simulated day
    """
    if number_of_staff < 1:
        raise ValueError("number_of_staff must be >= 1")
    if simulation_days < 1:
        raise ValueError("simulation_days must be >= 1")

    staff_master: List[Dict[str, Any]] = []
    daily_emotions: List[Dict[str, Any]] = []
    daily_behavior: List[Dict[str, Any]] = []

    for i in range(number_of_staff):
        # Stable, reproducible staff identifier
        staff_id = _deterministic_staff_id(restaurant_id, i)

        # Deterministic starting persona
        start_persona = _choose_persona_deterministically(
            weights=persona_weights,
            restaurant_id=restaurant_id,
            staff_index=i,
        )

        # Full lifecycle simulation
        lifecycle = simulate_staff_lifecycle(
            staff_id=staff_id,
            start_persona=start_persona,
            total_days=simulation_days,
            restaurant_profile=restaurant_profile,
        )

        # Determine final state
        if lifecycle and lifecycle[-1]["persona_after"] == "exit":
            final_persona = "exit"
            exit_day = lifecycle[-1]["day_index"] + 1  # human-readable day number
        else:
            final_persona = lifecycle[-1]["persona_after"] if lifecycle else start_persona
            exit_day = None

        # Staff master record
        staff_master.append({
            "staff_id": staff_id,
            "restaurant_id": restaurant_id,
            "start_persona": start_persona,
            "final_persona": final_persona,
            "total_days": len(lifecycle),
            "exit_day": exit_day,
        })

        # Flatten daily records
        for day_record in lifecycle:
            base = {
                "staff_id": staff_id,
                "restaurant_id": restaurant_id,
                "day_index": day_record["day_index"],
                "tenure_days": day_record["tenure_days"],
            }

            # Emotions now match organic schema exactly
            emotions = day_record["emotions"]
            daily_emotions.append({
                **base,
                "mood_emoji": emotions["mood_emoji"],
                "felt_safe": emotions["felt_safe"],
                "felt_fair": emotions["felt_fair"],
                "felt_respected": emotions["felt_respected"],
            })

            daily_behavior.append({
                **base,
                **day_record["behavior"],
            })

    return {
        "staff_master": staff_master,
        "daily_emotions": daily_emotions,
        "daily_behavior": daily_behavior,
    }