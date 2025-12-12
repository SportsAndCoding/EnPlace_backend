"""
modules/synthetic/staff_simulation_runner.py

Full-lifecycle simulator for a single synthetic staff member.
Chains emotion -> behavior -> persona evolution day-by-day.
All randomness is deterministic and reproducible.

UPDATED: Works with organic-matching schema:
- mood_emoji: 1-5 integer
- felt_safe, felt_fair, felt_respected: booleans
- Rolling averages convert booleans to rates
"""

from __future__ import annotations

import collections
from typing import Deque, List, Dict, Any

from modules.synthetic.daily_emotion_simulator import simulate_daily_emotions
from modules.synthetic.daily_behavior import simulate_daily_behavior
from modules.synthetic.persona_evolution import evolve_persona
from modules.synthetic.personas import PERSONA_DEFINITIONS, list_persona_keys


def _compute_rolling_averages(
    history: Deque[Dict[str, Any]],
    window: int = 30,
) -> Dict[str, float]:
    """
    Compute rolling averages from emotion history.
    
    For mood: average of mood_emoji values (1-5)
    For booleans: rate (percentage of True values)
    """
    if not history:
        return {
            "mood": 3.0,
            "safe_rate": 0.5,
            "fair_rate": 0.5,
            "respected_rate": 0.5,
        }

    recent = list(history)[-window:]
    n = len(recent)

    # Mood is averaged directly
    mood_avg = sum(d["mood_emoji"] for d in recent) / n
    
    # Booleans become rates (count of True / total)
    safe_rate = sum(1 for d in recent if d["felt_safe"]) / n
    fair_rate = sum(1 for d in recent if d["felt_fair"]) / n
    respected_rate = sum(1 for d in recent if d["felt_respected"]) / n

    return {
        "mood": mood_avg,
        "safe_rate": safe_rate,
        "fair_rate": fair_rate,
        "respected_rate": respected_rate,
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
        Unique identifier - used for deterministic randomness across all modules.
    start_persona : str
        Initial persona key (must exist in PERSONA_DEFINITIONS).
    total_days : int
        Maximum number of days to simulate.
    restaurant_profile : dict
        Restaurant configuration affecting behavior patterns.

    Returns
    -------
    list[dict]
        One dict per simulated day with:
        - staff_id, day_index, tenure_days
        - persona_before, persona_after, evolution_reason
        - emotions (output format: mood_emoji, felt_safe, felt_fair, felt_respected)
        - behavior
    """
    if start_persona not in PERSONA_DEFINITIONS:
        raise KeyError(
            f"Invalid start_persona '{start_persona}'. "
            f"Available keys: {list_persona_keys()}"
        )

    if total_days < 1:
        return []

    current_persona: str = start_persona
    previous_emotions: Dict[str, Any] | None = None
    tenure_days: int = 0
    records: List[Dict[str, Any]] = []

    # 30-day rolling window for persona evolution
    # Stores OUTPUT format (mood_emoji, felt_safe, felt_fair, felt_respected)
    emotion_history: Deque[Dict[str, Any]] = collections.deque(maxlen=30)

    for day_index in range(total_days):
        # ------------------------------------------------------------------
        # 1. Daily emotions
        # ------------------------------------------------------------------
        emotion_result = simulate_daily_emotions(
            persona_key=current_persona,
            previous_emotions=previous_emotions,
            day_index=day_index,
            staff_id=staff_id,
        )
        
        emotions_output = emotion_result["output"]
        emotion_history.append(emotions_output.copy())

        # ------------------------------------------------------------------
        # 2. Daily behavior
        # ------------------------------------------------------------------
        behavior = simulate_daily_behavior(
            staff_id=staff_id,
            persona_key=current_persona,
            emotional_state=emotions_output,
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
            rolling_mood=rolling["mood"],
            rolling_safe_rate=rolling["safe_rate"],
            rolling_fair_rate=rolling["fair_rate"],
            rolling_respected_rate=rolling["respected_rate"],
            staff_id=staff_id,
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
            "emotions": emotions_output,
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
                break

        # ------------------------------------------------------------------
        # 6. Next day preparation
        # ------------------------------------------------------------------
        previous_emotions = emotion_result  # Pass full result with internal state
        tenure_days += 1

    return records