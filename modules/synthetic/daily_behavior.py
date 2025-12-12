"""
modules/synthetic/daily_behavior.py

Simulates all non-emotional daily behavioral events for a staff member.
Returns attendance, swap behavior, OSM behavior, and drop requests.

UPDATED: Works with organic-matching schema:
- mood_emoji: 1-5 integer
- felt_safe, felt_fair, felt_respected: booleans

Removed stress/energy modifiers (not captured in organic check-ins).
Behavior now driven by mood + boolean flags.
"""

import random
import hashlib
from typing import Dict, Any

from modules.synthetic.personas import PERSONA_DEFINITIONS, list_persona_keys


def simulate_daily_behavior(
    *,
    staff_id: str,
    persona_key: str,
    emotional_state: Dict[str, Any],
    tenure_days: int,
    day_index: int,
    restaurant_profile: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Simulate all non-emotional daily behavioral events for a staff member.
    Returns attendance, swap behavior, OSM behavior, and drop requests.
    
    Parameters
    ----------
    emotional_state : dict
        Must contain: mood_emoji (1-5), felt_safe (bool), felt_fair (bool), felt_respected (bool)
    """
    if persona_key not in PERSONA_DEFINITIONS:
        raise KeyError(f"Unknown persona_key '{persona_key}'. Available: {list_persona_keys()}")

    # ------------------------------------------------------------------
    # Deterministic randomness (must remain EXACT)
    # ------------------------------------------------------------------
    seed_input = f"{staff_id}:{day_index}"
    seed = int(hashlib.sha1(seed_input.encode()).hexdigest(), 16)
    random.seed(day_index + (seed % 99991))

    # ------------------------------------------------------------------
    # Persona + emotional state
    # ------------------------------------------------------------------
    persona = PERSONA_DEFINITIONS[persona_key]
    att = persona["attendance_bias"]
    sched = persona["schedule_behavior"]

    # New schema fields
    mood = emotional_state["mood_emoji"]  # 1-5 scale
    felt_safe = emotional_state["felt_safe"]
    felt_fair = emotional_state["felt_fair"]
    felt_respected = emotional_state["felt_respected"]

    # ------------------------------------------------------------------
    # Restaurant profile validation
    # ------------------------------------------------------------------
    def _validate_profile(p: Dict[str, Any]) -> None:
        required_keys = [
            "volume_intensity",
            "guest_difficulty",
            "manager_fairness",
            "crew_cohesion",
            "burnout_multiplier",
            "tip_variance",
            "swap_culture",
        ]
        for k in required_keys:
            if k not in p:
                raise KeyError(f"restaurant_profile missing required key '{k}'")

    _validate_profile(restaurant_profile)

    vol = restaurant_profile["volume_intensity"]
    guest_diff = restaurant_profile["guest_difficulty"]
    mgr_fair = restaurant_profile["manager_fairness"]
    cohesion = restaurant_profile["crew_cohesion"]
    burnout_mult = restaurant_profile["burnout_multiplier"]
    tip_var = restaurant_profile["tip_variance"]
    swap_culture = restaurant_profile["swap_culture"]

    # ------------------------------------------------------------------
    # 1. Attendance probabilities with emotional + restaurant modifiers
    # ------------------------------------------------------------------
    late_prob = att["late_prob"]
    callout_prob = att["callout_prob"]
    ncns_prob = att["ncns_prob"]

    # Emotional modifiers (adapted for 1-5 mood scale + booleans)
    
    # Low mood (1-2 on 5-point scale) increases all negative behaviors
    if mood <= 2:
        late_prob = min(late_prob * 1.4, 0.95)
        callout_prob = min(callout_prob * 1.5, 0.95)
        ncns_prob = min(ncns_prob * 1.3, 0.95)
    elif mood <= 3:
        late_prob = min(late_prob * 1.2, 0.95)
        callout_prob = min(callout_prob * 1.2, 0.95)
    
    # Not feeling safe increases callouts (self-preservation)
    if not felt_safe:
        callout_prob = min(callout_prob * 1.4, 0.95)
        ncns_prob = min(ncns_prob * 1.2, 0.95)
    
    # Not feeling fairly treated increases NCNS (resentment)
    if not felt_fair:
        ncns_prob = min(ncns_prob * 1.5, 0.95)
        late_prob = min(late_prob * 1.2, 0.95)
    
    # Not feeling respected increases all negative behaviors
    if not felt_respected:
        late_prob = min(late_prob * 1.3, 0.95)
        callout_prob = min(callout_prob * 1.2, 0.95)
        ncns_prob = min(ncns_prob * 1.4, 0.95)

    # Restaurant modifiers
    late_prob = min(late_prob * (1.0 + vol), 0.95)
    callout_prob = min(callout_prob * burnout_mult, 0.95)
    ncns_prob = min(0.95, max(0.0, ncns_prob))

    # Raw events
    raw_late = random.random() < late_prob
    raw_call_out = random.random() < callout_prob
    raw_ncns = random.random() < ncns_prob

    # Mutual exclusion: NCNS > Call-out > Late
    if raw_ncns:
        call_out = False
        late_arrival = False
        no_call_no_show = True
    elif raw_call_out:
        call_out = True
        late_arrival = False
        no_call_no_show = False
    else:
        call_out = False
        late_arrival = raw_late
        no_call_no_show = False

    # Late minutes
    late_minutes = random.randint(3, 35) if late_arrival else None

    # Early departure (based on mood + safety)
    early_departure = None
    if not (call_out or no_call_no_show):
        # Base probability from mood (inverted: low mood = higher prob)
        base_prob = (5 - mood) / 20.0  # mood=1 → 0.2, mood=5 → 0.0
        if not felt_safe:
            base_prob = min(0.95, base_prob * 1.5)
        base_prob = min(0.95, base_prob * (1.0 + guest_diff))
        early_departure = random.random() < base_prob

    # Call-out reason
    call_out_reason = None
    if call_out:
        call_out_reason = random.choice(
            ["sick", "family_emergency", "transportation", "mental_health"]
        )

    # ------------------------------------------------------------------
    # 2. Swap request behavior
    # ------------------------------------------------------------------
    swap_prob = sched["swap_request_prob"]

    # Emotional modifiers
    if not felt_fair:
        swap_prob = min(swap_prob * 1.3, 0.95)
    if not felt_respected:
        swap_prob = min(swap_prob * 1.2, 0.95)
    if mood >= 4:
        swap_prob = max(0.0, swap_prob * 0.8)  # Happy people swap less

    # Restaurant modifier
    swap_prob = min(0.95, max(0.0, swap_prob * swap_culture))

    swap_requested = 1 if random.random() < swap_prob else 0

    # swap_approved
    base_approve = 0.7 + (cohesion - 0.5) * 0.4
    base_approve = max(0.3, min(0.95, base_approve))
    swap_approved = 1 if swap_requested and random.random() < base_approve else 0
    swap_denied = swap_requested - swap_approved

    # ------------------------------------------------------------------
    # 3. Drop request behavior
    # ------------------------------------------------------------------
    drop_prob = sched["drop_request_prob"]

    # Emotional modifiers
    if mood <= 2:
        drop_prob = min(drop_prob * 1.4, 0.95)
    if not felt_safe:
        drop_prob = min(drop_prob * 1.3, 0.95)

    # Restaurant modifier
    drop_prob = min(0.95, drop_prob * (1.0 + vol))

    drop_requested = 1 if random.random() < drop_prob else 0

    # ------------------------------------------------------------------
    # 4. OSM (Open Shift Market)
    # ------------------------------------------------------------------
    num_offers = random.choice([0, 0, 1, 1, 1, 2, 2, 3])

    accept_prob = sched["osm_offer_accept_prob"]

    # Emotional modifiers
    if mood >= 4:
        accept_prob = min(1.0, accept_prob * 1.2)  # Happy = more willing
    if not felt_fair:
        accept_prob = max(0.0, accept_prob * 0.7)  # Unfair = less willing
    if not felt_respected:
        accept_prob = max(0.0, accept_prob * 0.75)

    # Restaurant modifier
    accept_prob = min(1.0, max(0.0, accept_prob * (1.0 + tip_var)))

    osm_offers_accepted = sum(random.random() < accept_prob for _ in range(num_offers))
    osm_offers_declined = num_offers - osm_offers_accepted

    # ------------------------------------------------------------------
    # 5. Return schema EXACTLY unchanged
    # ------------------------------------------------------------------
    return {
        "late_arrival": late_arrival if not (call_out or no_call_no_show) else None,
        "late_minutes": late_minutes,
        "early_departure": early_departure if not (call_out or no_call_no_show) else None,
        "call_out": call_out if not no_call_no_show else False,
        "call_out_reason": call_out_reason,
        "no_call_no_show": no_call_no_show,
        "swap_requested": swap_requested,
        "swap_approved": swap_approved,
        "swap_denied": swap_denied,
        "drop_requested": drop_requested,
        "osm_offers_accepted": osm_offers_accepted,
        "osm_offers_declined": osm_offers_declined,
    }