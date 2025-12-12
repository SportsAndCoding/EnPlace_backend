"""
modules/synthetic/daily_emotion_simulator.py

Simulates one day of emotional check-in data for a synthetic staff member.

UPDATED: Output now matches organic check-in schema exactly:
- mood_emoji: integer 1-5 (matches app emoji picker)
- felt_safe: boolean
- felt_fair: boolean
- felt_respected: boolean

Internal calculations use continuous probabilities, then convert to final output.
"""

import random
from typing import Dict, Any

from modules.synthetic.personas import PERSONA_DEFINITIONS, list_persona_keys


def simulate_daily_emotions(
    *,
    persona_key: str,
    previous_emotions: Dict[str, Any] | None,
    day_index: int,
    staff_id: str = "unknown",
) -> Dict[str, Any]:
    """
    Simulate one day of emotional state for a staff member based on their persona.

    Parameters
    ----------
    persona_key: str
        Key identifying the persona (must exist in PERSONA_DEFINITIONS).
    previous_emotions: dict | None
        Previous day's internal state (mood_raw, safe_prob, fair_prob, respected_prob).
        If None or malformed, the persona baseline is used as starting point.
    day_index: int
        Current day index (0-based). Used for deterministic randomness.
    staff_id: str
        Staff identifier for deterministic randomness across days.

    Returns
    -------
    dict with two sections:
        "output": Final check-in values matching organic schema
            - mood_emoji: int (1-5)
            - felt_safe: bool
            - felt_fair: bool
            - felt_respected: bool
        "internal": Raw probabilities for next-day calculation
            - mood_raw: float (1.0-5.0)
            - safe_prob: float (0.0-1.0)
            - fair_prob: float (0.0-1.0)
            - respected_prob: float (0.0-1.0)
    """
    if persona_key not in PERSONA_DEFINITIONS:
        raise KeyError(
            f"Unknown persona_key '{persona_key}'. "
            f"Available keys: {list_persona_keys()}"
        )

    persona = PERSONA_DEFINITIONS[persona_key]
    baseline = persona["baseline"]
    volatility = persona["volatility"]
    inertia = persona["inertia"]

    # Use previous internal values if valid, otherwise fall back to baseline
    prev = previous_emotions.get("internal", {}) if isinstance(previous_emotions, dict) else {}
    
    prev_mood = prev.get("mood_raw", baseline["mood"])
    prev_safe = prev.get("safe_prob", baseline["felt_safe_prob"])
    prev_fair = prev.get("fair_prob", baseline["felt_fair_prob"])
    prev_respected = prev.get("respected_prob", baseline["felt_respected_prob"])

    # Deterministic randomness: unique per staff member per day
    seed_str = f"{staff_id}:{day_index}:emotions"
    seed_val = hash(seed_str) % (2**31)
    random.seed(seed_val)

    def compute_continuous(field: str, prev_val: float, baseline_val: float, 
                           min_val: float, max_val: float) -> float:
        """Compute next value with inertia, baseline pull, and noise."""
        noise = random.uniform(-1.0, 1.0)
        raw = (
            inertia[field] * prev_val
            + (1.0 - inertia[field]) * baseline_val
            + volatility[field] * noise
        )
        return max(min_val, min(max_val, raw))

    # Calculate internal continuous values
    mood_raw = compute_continuous(
        "mood", prev_mood, baseline["mood"], 1.0, 5.0
    )
    safe_prob = compute_continuous(
        "felt_safe_prob", prev_safe, baseline["felt_safe_prob"], 0.0, 1.0
    )
    fair_prob = compute_continuous(
        "felt_fair_prob", prev_fair, baseline["felt_fair_prob"], 0.0, 1.0
    )
    respected_prob = compute_continuous(
        "felt_respected_prob", prev_respected, baseline["felt_respected_prob"], 0.0, 1.0
    )

    # Convert to output format (what would appear in a check-in)
    # Mood: round to nearest integer 1-5
    mood_emoji = max(1, min(5, round(mood_raw)))
    
    # Booleans: probabilistic based on internal probability
    felt_safe = random.random() < safe_prob
    felt_fair = random.random() < fair_prob
    felt_respected = random.random() < respected_prob

    return {
        "output": {
            "mood_emoji": mood_emoji,
            "felt_safe": felt_safe,
            "felt_fair": felt_fair,
            "felt_respected": felt_respected,
        },
        "internal": {
            "mood_raw": round(mood_raw, 3),
            "safe_prob": round(safe_prob, 3),
            "fair_prob": round(fair_prob, 3),
            "respected_prob": round(respected_prob, 3),
        }
    }