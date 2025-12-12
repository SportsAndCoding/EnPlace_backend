"""
modules/synthetic/manager_simulation.py

Generates synthetic manager daily logs based on actual staff emotions.
Manager personas determine how aligned the manager's perception is with staff reality.

UPDATED: More variance in ratings to create meaningful SMA differentiation.
"""

import random
import hashlib
from dataclasses import dataclass
from typing import Dict, List, Any, Optional


@dataclass
class ManagerPersona:
    """Defines how a manager perceives their restaurant."""
    persona_type: str
    
    # Rating bias: added to actual staff avg mood
    rating_bias: float  # -1.5 to +1.0
    
    # Alignment: how closely rating tracks staff mood (0 = random, 1 = perfect)
    alignment: float  # 0.0 to 1.0
    
    # Rating variance: how much the rating bounces around
    rating_variance: float  # 0.3 to 1.5
    
    # Sensitivity to problems
    chaos_sensitivity: float  # How likely to notice chaos (0-1)
    staffing_awareness: float  # How likely to notice staffing issues (0-1)
    
    # Logging consistency: probability of logging each day
    log_rate: float  # 0.5 to 1.0


# Manager persona definitions - MORE VARIANCE
MANAGER_PERSONAS = {
    "aligned": ManagerPersona(
        persona_type="aligned",
        rating_bias=0.0,
        alignment=0.85,
        rating_variance=0.5,  # Low variance, tracks staff mood
        chaos_sensitivity=0.8,
        staffing_awareness=0.85,
        log_rate=0.95,
    ),
    "optimistic": ManagerPersona(
        persona_type="optimistic",
        rating_bias=0.8,  # Sees things rosier
        alignment=0.5,
        rating_variance=0.6,
        chaos_sensitivity=0.3,  # Doesn't notice chaos
        staffing_awareness=0.5,
        log_rate=0.85,
    ),
    "pessimistic": ManagerPersona(
        persona_type="pessimistic",
        rating_bias=-1.2,  # Sees things worse (will generate 1s and 2s)
        alignment=0.5,
        rating_variance=0.8,  # More variance
        chaos_sensitivity=0.95,  # Hyper-aware of problems
        staffing_awareness=0.9,
        log_rate=0.90,
    ),
    "oblivious": ManagerPersona(
        persona_type="oblivious",
        rating_bias=0.3,
        alignment=0.15,  # Very low alignment - out of touch
        rating_variance=1.2,  # High variance, random
        chaos_sensitivity=0.2,
        staffing_awareness=0.3,
        log_rate=0.70,
    ),
    "micromanager": ManagerPersona(
        persona_type="micromanager",
        rating_bias=-0.8,  # Critical
        alignment=0.6,
        rating_variance=1.0,  # Volatile ratings
        chaos_sensitivity=0.99,  # Sees problems everywhere
        staffing_awareness=0.95,
        log_rate=0.99,
    ),
    "burned_out": ManagerPersona(
        persona_type="burned_out",
        rating_bias=-0.5,
        alignment=0.3,  # Disconnected
        rating_variance=1.3,  # Very inconsistent
        chaos_sensitivity=0.4,
        staffing_awareness=0.4,
        log_rate=0.60,  # Often forgets to log
    ),
}


def assign_manager_persona(restaurant_id: int) -> ManagerPersona:
    """
    Assign a manager persona to a restaurant based on restaurant_id.
    Distribution:
    - 30% aligned (good managers)
    - 20% optimistic
    - 18% pessimistic (will generate low ratings)
    - 15% oblivious
    - 10% micromanager
    - 7% burned_out
    """
    seed = int(hashlib.sha256(f"manager:{restaurant_id}".encode()).hexdigest(), 16)
    roll = (seed % 100) / 100
    
    if roll < 0.30:
        return MANAGER_PERSONAS["aligned"]
    elif roll < 0.50:
        return MANAGER_PERSONAS["optimistic"]
    elif roll < 0.68:
        return MANAGER_PERSONAS["pessimistic"]
    elif roll < 0.83:
        return MANAGER_PERSONAS["oblivious"]
    elif roll < 0.93:
        return MANAGER_PERSONAS["micromanager"]
    else:
        return MANAGER_PERSONAS["burned_out"]


def _deterministic_random(restaurant_id: int, day_index: int, salt: str = "") -> float:
    """Generate deterministic random [0,1) based on inputs."""
    seed_str = f"{restaurant_id}:{day_index}:{salt}"
    hash_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    return (hash_val % 1_000_000) / 1_000_000


def _deterministic_normal(restaurant_id: int, day_index: int, salt: str = "") -> float:
    """Generate deterministic value approximating normal distribution (-2 to +2 range)."""
    # Use Box-Muller-like approximation with deterministic randoms
    u1 = max(0.001, _deterministic_random(restaurant_id, day_index, salt + "_u1"))
    u2 = _deterministic_random(restaurant_id, day_index, salt + "_u2")
    
    # Approximate normal using sum of uniforms (central limit theorem)
    # Sum of 4 uniforms, centered and scaled
    u3 = _deterministic_random(restaurant_id, day_index, salt + "_u3")
    u4 = _deterministic_random(restaurant_id, day_index, salt + "_u4")
    
    normal_approx = (u1 + u2 + u3 + u4 - 2) * 1.5  # Range roughly -3 to +3
    return max(-2.5, min(2.5, normal_approx))


def generate_manager_log(
    restaurant_id: int,
    day_index: int,
    staff_emotions: List[Dict[str, Any]],
    staff_behaviors: List[Dict[str, Any]],
    persona: ManagerPersona,
) -> Optional[Dict[str, Any]]:
    """
    Generate a manager's daily log based on staff reality and manager persona.
    """
    
    # Check if manager logs today
    if _deterministic_random(restaurant_id, day_index, "log") >= persona.log_rate:
        return None
    
    # ═══════════════════════════════════════════════════════════════
    # CALCULATE STAFF REALITY
    # ═══════════════════════════════════════════════════════════════
    
    if staff_emotions:
        moods = [e.get("mood_emoji", 3) for e in staff_emotions if e.get("mood_emoji")]
        staff_avg_mood = sum(moods) / len(moods) if moods else 3.0
    else:
        staff_avg_mood = 3.0
    
    if staff_emotions:
        total = len(staff_emotions)
        safe_rate = sum(1 for e in staff_emotions if e.get("felt_safe")) / total
        fair_rate = sum(1 for e in staff_emotions if e.get("felt_fair")) / total
        respected_rate = sum(1 for e in staff_emotions if e.get("felt_respected")) / total
    else:
        safe_rate = fair_rate = respected_rate = 0.7
    
    if staff_behaviors:
        callouts = sum(1 for b in staff_behaviors if b.get("callout"))
        ncns = sum(1 for b in staff_behaviors if b.get("ncns"))
        late_count = sum(1 for b in staff_behaviors if b.get("late"))
        total_staff = len(staff_behaviors)
    else:
        callouts = ncns = late_count = 0
        total_staff = 10
    
    # ═══════════════════════════════════════════════════════════════
    # GENERATE MANAGER RATING WITH VARIANCE
    # ═══════════════════════════════════════════════════════════════
    
    # Base rating from staff mood (aligned portion)
    aligned_rating = staff_avg_mood + persona.rating_bias
    
    # Random baseline (unaligned portion) - uses normal distribution
    random_component = 3.0 + _deterministic_normal(restaurant_id, day_index, "baseline") * 1.0
    
    # Blend aligned vs random based on alignment factor
    blended_rating = (
        persona.alignment * aligned_rating +
        (1 - persona.alignment) * random_component
    )
    
    # Add daily variance (noise)
    noise = _deterministic_normal(restaurant_id, day_index, "noise") * persona.rating_variance
    raw_rating = blended_rating + noise
    
    # Problem modifier: callouts, ncns, low mood drag rating down
    problem_modifier = 0
    if ncns >= 1:
        problem_modifier -= 1.0
    if callouts >= 2:
        problem_modifier -= 0.5
    if staff_avg_mood < 2.5:
        problem_modifier -= 0.5
    if fair_rate < 0.5:
        problem_modifier -= 0.3
    
    # Apply problem modifier based on chaos sensitivity
    raw_rating += problem_modifier * persona.chaos_sensitivity
    
    # Clamp and round
    overall_rating = max(1, min(5, round(raw_rating)))
    
    # ═══════════════════════════════════════════════════════════════
    # BOOLEAN PERCEPTIONS
    # ═══════════════════════════════════════════════════════════════
    
    # felt_smooth: high mood, low issues, high rating
    smooth_reality = (
        staff_avg_mood >= 3.5 and
        ncns == 0 and
        callouts <= 1 and
        fair_rate >= 0.7 and
        overall_rating >= 4
    )
    # Smooth perception depends on alignment
    if smooth_reality:
        felt_smooth = _deterministic_random(restaurant_id, day_index, "smooth") < (0.5 + persona.alignment * 0.4)
    else:
        felt_smooth = _deterministic_random(restaurant_id, day_index, "smooth") < 0.1
    
    # felt_understaffed: callouts, ncns
    understaffed_reality = callouts >= 2 or ncns >= 1 or (callouts >= 1 and total_staff < 8)
    felt_understaffed = (
        understaffed_reality and 
        _deterministic_random(restaurant_id, day_index, "understaffed") < persona.staffing_awareness
    )
    
    # felt_chaotic: ncns, low mood, lots of issues
    chaos_reality = (
        ncns >= 1 or
        staff_avg_mood < 2.5 or
        (callouts >= 2 and late_count >= 2) or
        fair_rate < 0.4 or
        overall_rating <= 2
    )
    felt_chaotic = (
        chaos_reality and
        _deterministic_random(restaurant_id, day_index, "chaos") < persona.chaos_sensitivity
    )
    
    # felt_overstaffed: rare
    felt_overstaffed = _deterministic_random(restaurant_id, day_index, "overstaffed") < 0.03
    
    # Consistency checks
    if felt_smooth and felt_chaotic:
        if persona.chaos_sensitivity > 0.6:
            felt_smooth = False
        else:
            felt_chaotic = False
    
    if overall_rating <= 2 and felt_smooth:
        felt_smooth = False
    
    if overall_rating >= 4 and felt_chaotic and not chaos_reality:
        felt_chaotic = False
    
    return {
        "restaurant_id": restaurant_id,
        "manager_id": f"MGR_{restaurant_id}",
        "day_index": day_index,
        "overall_rating": overall_rating,
        "felt_smooth": felt_smooth,
        "felt_understaffed": felt_understaffed,
        "felt_chaotic": felt_chaotic,
        "felt_overstaffed": felt_overstaffed,
    }


def generate_restaurant_manager_logs(
    restaurant_id: int,
    daily_emotions: List[Dict[str, Any]],
    daily_behaviors: List[Dict[str, Any]],
    total_days: int = 365,
) -> List[Dict[str, Any]]:
    """
    Generate all manager logs for a restaurant's simulation.
    """
    
    persona = assign_manager_persona(restaurant_id)
    
    # Index emotions and behaviors by day
    emotions_by_day = {}
    for e in daily_emotions:
        day = e.get("day_index")
        if day not in emotions_by_day:
            emotions_by_day[day] = []
        emotions_by_day[day].append(e)
    
    behaviors_by_day = {}
    for b in daily_behaviors:
        day = b.get("day_index")
        if day not in behaviors_by_day:
            behaviors_by_day[day] = []
        behaviors_by_day[day].append(b)
    
    # Generate logs for each day
    logs = []
    for day_index in range(1, total_days + 1):
        day_emotions = emotions_by_day.get(day_index, [])
        day_behaviors = behaviors_by_day.get(day_index, [])
        
        log = generate_manager_log(
            restaurant_id=restaurant_id,
            day_index=day_index,
            staff_emotions=day_emotions,
            staff_behaviors=day_behaviors,
            persona=persona,
        )
        
        if log:
            logs.append(log)
    
    return logs