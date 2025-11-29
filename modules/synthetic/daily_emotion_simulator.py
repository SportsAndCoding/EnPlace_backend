import random
from typing import Dict, Any

from modules.synthetic.personas import PERSONA_DEFINITIONS, list_persona_keys


def simulate_daily_emotions(
    *,
    persona_key: str,
    previous_emotions: Dict[str, float] | None,
    day_index: int,
) -> Dict[str, float]:
    """
    Simulate one day of emotional state for a staff member based on their persona.

    Parameters
    ----------
    persona_key: str
        Key identifying the persona (must exist in PERSONA_DEFINITIONS).
    previous_emotions: dict | None
        Previous day's values for mood, stress, energy, fairness.
        If None or malformed, the persona baseline is used as starting point.
    day_index: int
        Current day index (0-based). Used only for deterministic randomness.

    Returns
    -------
    dict
        Daily emotional state with keys "mood", "stress", "energy", "fairness".
        All values are floats in the range [0.0, 10.0] rounded to 2 decimals.
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

    # Use previous values if valid, otherwise fall back to baseline
    prev = previous_emotions if isinstance(previous_emotions, dict) else {}
    prev_mood = prev.get("mood", baseline["mood"])
    prev_stress = prev.get("stress", baseline["stress"])
    prev_energy = prev.get("energy", baseline["energy"])
    prev_fairness = prev.get("fairness", baseline["fairness"])

    # Deterministic per-staff-member, per-day randomness (kept EXACTLY as-is)
    random.seed(day_index + (hash(persona_key) % 99991))

    def compute(field: str, prev_val: float, baseline_val: float) -> float:
        noise = random.uniform(-1.0, 1.0)
        raw = (
            inertia[field] * prev_val
            + (1.0 - inertia[field]) * baseline_val
            + volatility[field] * noise
        )
        return max(0.0, min(10.0, raw))

    result = {
        "mood": round(
            compute("mood", prev_mood, baseline["mood"]), 2
        ),
        "stress": round(
            compute("stress", prev_stress, baseline["stress"]), 2
        ),
        "energy": round(
            compute("energy", prev_energy, baseline["energy"]), 2
        ),
        "fairness": round(
            compute("fairness", prev_fairness, baseline["fairness"]), 2
        ),
    }

    return result
