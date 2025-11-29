"""
modules/synthetic/staff_simulation_runner.py

Full-lifecycle simulator for a single synthetic staff member.
Chains emotion → behavior → persona evolution day-by-day.
All randomness is deterministic and reproducible.
"""

from __future__ import annotations

import collections
from typing import Deque, List, Dict, Any

# Correct import path – project uses modules/synthetic/
from modules.synthetic.daily_emotion_simulator import simulate_daily_emotions
from modules.synthetic.daily_behavior import simulate_daily_behavior
from modules.synthetic.persona_evolution import evolve_persona
from modules.synthetic.personas import PERSONA_DEFINITIONS, list_persona_keys


def _compute_rolling_averages(
    history: Deque[Dict[str, float]],
    window: int = 30,
) -> Dict[str, float]:
    """Return 30-day rolling averages (or fewer if not enough data)."""
    if not history:
        return {"mood": 0.0, "stress": 0.0, "energy": 0.0, "fairness": 0.0}

    recent = list(history)[-window:]
    n = len(recent)

    return {
        "mood": sum(d["mood"] for d in recent) / n,
        "stress": sum(d["stress"] for d in recent) / n,
        "energy": sum(d["energy"] for d in recent) / n,
        "fairness": sum(d["fairness"] for d in recent) / n,
    }


def simulate_staff_lifecycle(
    *,
    staff_id: str,
    start_persona: str,
    total_days: int,
    restaurant_profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Simulate the entire employment history of one staff member.

    Parameters
    ----------
    staff_id : str
        Unique identifier – used for deterministic randomness across all modules.
    start_persona : str
        Initial persona key (must exist in PERSONA_DEFINITIONS).
    total_days : int
        Maximum number of days to simulate.

    Returns
    -------
    list[dict]
        One dict per simulated day with staff_id, day_index, tenure_days,
        persona_before/after, emotions, behavior, and evolution_reason.
    """
    if start_persona not in PERSONA_DEFINITIONS:
        raise KeyError(
            f"Invalid start_persona '{start_persona}'. "
            f"Available keys: {list_persona_keys()}"
        )

    if total_days < 1:
        return []

    current_persona: str = start_persona
    previous_emotions: Dict[str, float] | None = None
    tenure_days: int = 0
    records: List[Dict[str, Any]] = []

    # 30-day rolling window for persona evolution
    emotion_history: Deque[Dict[str, float]] = collections.deque(maxlen=30)

    for day_index in range(total_days):
        # ------------------------------------------------------------------
        # 1. Daily emotions
        # ------------------------------------------------------------------
        emotions = simulate_daily_emotions(
            persona_key=current_persona,
            previous_emotions=previous_emotions,
            day_index=day_index,
        )
        emotion_history.append(emotions.copy())

        # ------------------------------------------------------------------
        # 2. Daily behavior
        # ------------------------------------------------------------------
        behavior = simulate_daily_behavior(
            staff_id=staff_id,
            persona_key=current_persona,
            emotional_state=emotions,
            tenure_days=tenure_days,
            day_index=day_index,
            restaurant_profile=restaurant_profile,
        )

        # ------------------------------------------------------------------
        # 3. Persona evolution (30-day rolling averages)
        # ------------------------------------------------------------------
        rolling = _compute_rolling_averages(emotion_history)

        evolution = evolve_persona(
            current_persona=current_persona,
            tenure_days=tenure_days,
            rolling_mood_avg=rolling["mood"],
            rolling_stress_avg=rolling["stress"],
            rolling_energy_avg=rolling["energy"],
            rolling_fairness_avg=rolling["fairness"],
        )

        new_persona = evolution["new_persona"]
        reason = evolution["reason"]

        # ------------------------------------------------------------------
        # 4. Daily record
        # ------------------------------------------------------------------
        daily_record: Dict[str, Any] = {
            "staff_id": staff_id,
            "day_index": day_index,
            "tenure_days": tenure_days,
            "persona_before": current_persona,
            "persona_after": new_persona,
            "emotions": emotions,
            "behavior": behavior,
            "evolution_reason": reason,
        }

        records.append(daily_record)

        # ------------------------------------------------------------------
        # 5. Apply persona change / exit
        # ------------------------------------------------------------------
        if evolution["changed"]:
            current_persona = new_persona
            if new_persona == "exit":
                # Exit day already recorded – stop simulation early
                break

        # ------------------------------------------------------------------
        # 6. Next day preparation
        # ------------------------------------------------------------------
        previous_emotions = emotions
        tenure_days += 1

    return records