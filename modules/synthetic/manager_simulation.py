"""
modules/synthetic/manager_simulation.py

Generates synthetic manager daily logs based on actual staff emotions.
Manager personas determine how aligned the manager's perception is with staff reality.
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
    rating_bias: float  # -1.0 to +1.0
    
    # Alignment: how closely rating tracks staff mood (0 = random, 1 = perfect)
    alignment: float  # 0.0 to 1.0
    
    # Sensitivity to problems
    chaos_sensitivity: float  # How likely to notice chaos (0-1)
    staffing_awareness: float  # How likely to notice staffing issues (0-1)
    
    # Logging consistency: probability of logging each day
    log_rate: float  # 0.5 to 1.0


# Manager persona definitions
MANAGER_PERSONAS = {
    "aligned": ManagerPersona(
        persona_type="aligned",
        rating_bias=0.0,
        alignment=0.9,
        chaos_sensitivity=0.8,
        staffing_awareness=0.85,
        log_rate=0.95,
    ),
    "optimistic": ManagerPersona(
        persona_type="optimistic",
        rating_bias=0.7,
        alignment=0.6,
        chaos_sensitivity=0.4,  # Doesn't notice chaos as much
        staffing_awareness=0.6,
        log_rate=0.85,
    ),
    "pessimistic": ManagerPersona(
        persona_type="pessimistic",
        rating_bias=-0.6,
        alignment=0.6,
        chaos_sensitivity=0.95,  # Hyper-aware of problems
        staffing_awareness=0.9,
        log_rate=0.90,
    ),
    "oblivious": ManagerPersona(
        persona_type="oblivious",
        rating_bias=0.3,
        alignment=0.2,  # Low alignment - out of touch
        chaos_sensitivity=0.3,
        staffing_awareness=0.4,
        log_rate=0.70,  # Logs less frequently
    ),
    "micromanager": ManagerPersona(
        persona_type="micromanager",
        rating_bias=-0.3,
        alignment=0.7,
        chaos_sensitivity=0.99,  # Sees problems everywhere
        staffing_awareness=0.95,
        log_rate=0.99,  # Logs every single day
    ),
}


def assign_manager_persona(restaurant_id: int) -> ManagerPersona:
    """
    Assign a manager persona to a restaurant based on restaurant_id.
    Distribution:
    - 40% aligned (good managers)
    - 20% optimistic
    - 15% pessimistic
    - 15% oblivious
    - 10% micromanager
    """
    # Deterministic based on restaurant_id
    seed = int(hashlib.sha256(f"manager:{restaurant_id}".encode()).hexdigest(), 16)
    roll = (seed % 100) / 100
    
    if roll < 0.40:
        return MANAGER_PERSONAS["aligned"]
    elif roll < 0.60:
        return MANAGER_PERSONAS["optimistic"]
    elif roll < 0.75:
        return MANAGER_PERSONAS["pessimistic"]
    elif roll < 0.90:
        return MANAGER_PERSONAS["oblivious"]
    else:
        return MANAGER_PERSONAS["micromanager"]


def _deterministic_random(restaurant_id: int, day_index: int, salt: str = "") -> float:
    """Generate deterministic random [0,1) based on inputs."""
    seed_str = f"{restaurant_id}:{day_index}:{salt}"
    hash_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    return (hash_val % 1_000_000) / 1_000_000


def generate_manager_log(
    restaurant_id: int,
    day_index: int,
    staff_emotions: List[Dict[str, Any]],
    staff_behaviors: List[Dict[str, Any]],
    persona: ManagerPersona,
) -> Optional[Dict[str, Any]]:
    """
    Generate a manager's daily log based on staff reality and manager persona.
    
    Args:
        restaurant_id: Restaurant ID
        day_index: Day of simulation
        staff_emotions: List of staff emotion records for the day
        staff_behaviors: List of staff behavior records for the day
        persona: Manager's persona
        
    Returns:
        Manager log dict, or None if manager didn't log today
    """
    
    # Check if manager logs today
    if _deterministic_random(restaurant_id, day_index, "log") >= persona.log_rate:
        return None
    
    # ═══════════════════════════════════════════════════════════════
    # CALCULATE STAFF REALITY
    # ═══════════════════════════════════════════════════════════════
    
    # Average staff mood (1-5)
    if staff_emotions:
        moods = [e.get("mood_emoji", 3) for e in staff_emotions if e.get("mood_emoji")]
        staff_avg_mood = sum(moods) / len(moods) if moods else 3.0
    else:
        staff_avg_mood = 3.0
    
    # Staff felt_* rates
    if staff_emotions:
        total = len(staff_emotions)
        safe_rate = sum(1 for e in staff_emotions if e.get("felt_safe")) / total
        fair_rate = sum(1 for e in staff_emotions if e.get("felt_fair")) / total
        respected_rate = sum(1 for e in staff_emotions if e.get("felt_respected")) / total
    else:
        safe_rate = fair_rate = respected_rate = 0.7
    
    # Behavior issues
    if staff_behaviors:
        callouts = sum(1 for b in staff_behaviors if b.get("callout"))
        ncns = sum(1 for b in staff_behaviors if b.get("ncns"))
        late_count = sum(1 for b in staff_behaviors if b.get("late"))
        total_staff = len(staff_behaviors)
    else:
        callouts = ncns = late_count = 0
        total_staff = 10  # Default
    
    # ═══════════════════════════════════════════════════════════════
    # GENERATE MANAGER PERCEPTION
    # ═══════════════════════════════════════════════════════════════
    
    # Overall rating: blend of reality and bias
    noise = (_deterministic_random(restaurant_id, day_index, "noise") - 0.5) * 1.0
    
    # Aligned portion tracks staff mood, unaligned portion is biased baseline
    aligned_rating = staff_avg_mood + persona.rating_bias
    baseline_rating = 3.5 + persona.rating_bias
    
    raw_rating = (
        persona.alignment * aligned_rating +
        (1 - persona.alignment) * baseline_rating +
        noise * (1 - persona.alignment)  # More noise for less aligned managers
    )
    
    overall_rating = max(1, min(5, round(raw_rating)))
    
    # ═══════════════════════════════════════════════════════════════
    # BOOLEAN PERCEPTIONS
    # ═══════════════════════════════════════════════════════════════
    
    # felt_smooth: high mood, low issues
    smooth_reality = (
        staff_avg_mood >= 3.5 and
        ncns == 0 and
        callouts <= 1 and
        fair_rate >= 0.7
    )
    felt_smooth = smooth_reality if _deterministic_random(restaurant_id, day_index, "smooth") < persona.alignment else (
        _deterministic_random(restaurant_id, day_index, "smooth2") < 0.5
    )
    
    # felt_understaffed: callouts, low headcount
    understaffed_reality = callouts >= 2 or (callouts >= 1 and total_staff < 8)
    felt_understaffed = (
        understaffed_reality and 
        _deterministic_random(restaurant_id, day_index, "understaffed") < persona.staffing_awareness
    )
    
    # felt_chaotic: ncns, low mood, lots of issues
    chaos_reality = (
        ncns >= 1 or
        staff_avg_mood < 2.5 or
        (callouts >= 2 and late_count >= 2) or
        fair_rate < 0.4
    )
    felt_chaotic = (
        chaos_reality and
        _deterministic_random(restaurant_id, day_index, "chaos") < persona.chaos_sensitivity
    )
    
    # felt_overstaffed: rare, random low probability
    felt_overstaffed = _deterministic_random(restaurant_id, day_index, "overstaffed") < 0.05
    
    # Consistency: can't feel both smooth and chaotic
    if felt_smooth and felt_chaotic:
        # Chaos wins if manager is sensitive to it
        if persona.chaos_sensitivity > 0.6:
            felt_smooth = False
        else:
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
    
    Args:
        restaurant_id: Restaurant ID
        daily_emotions: All emotion records for the restaurant
        daily_behaviors: All behavior records for the restaurant
        total_days: Number of days to simulate
        
    Returns:
        List of manager log dicts
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