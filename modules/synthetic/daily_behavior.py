import random
import hashlib
from typing import Dict, Any

from modules.synthetic.personas import PERSONA_DEFINITIONS, list_persona_keys


def simulate_daily_behavior(
    *,
    staff_id: str,
    persona_key: str,
    emotional_state: Dict[str, float],
    tenure_days: int,
    day_index: int,
    restaurant_profile: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Simulate all non-emotional daily behavioral events for a staff member.
    Returns attendance, swap behavior, OSM behavior, and drop requests.
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

    mood = emotional_state["mood"]
    stress = emotional_state["stress"]
    energy = emotional_state["energy"]
    fairness = emotional_state["fairness"]

    # ------------------------------------------------------------------
    # Restaurant profile validation (MUST use only existing keys)
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
    # 1. Attendance probabilities w/ emotional + restaurant modifiers
    # ------------------------------------------------------------------
    late_prob = att["late_prob"]
    callout_prob = att["callout_prob"]
    ncns_prob = att["ncns_prob"]

    # Emotional modifiers (unchanged)
    if stress > 7.0:
        late_prob = min(late_prob * 1.4, 0.95)
        callout_prob = min(callout_prob * 1.4, 0.95)
    if energy < 4.0:
        callout_prob = min(callout_prob * 1.5, 0.95)
    if fairness < 5.0:
        ncns_prob = min(ncns_prob * 1.3, 0.95)
    if mood < 4.0:
        late_prob = min(late_prob * 1.2, 0.95)
        callout_prob = min(callout_prob * 1.2, 0.95)
        ncns_prob = min(ncns_prob * 1.2, 0.95)

    # REQUIRED RESTAURANT MODIFIERS
    late_prob = min(late_prob * (1.0 + vol), 0.95)
    callout_prob = min(callout_prob * burnout_mult, 0.95)
    ncns_prob *= 1.0
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

    # Early departure (emotional + restaurant modifier)
    early_departure = None
    if not (call_out or no_call_no_show):
        base_prob = (stress / 12.0)
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

    # Emotional modifiers (unchanged)
    if fairness < 5.0:
        swap_prob = min(swap_prob * 1.2, 0.95)
    if stress > 7.0:
        swap_prob = min(swap_prob * 1.2, 0.95)
    if energy > 7.5:
        swap_prob = max(0.0, swap_prob * 0.8)

    # REQUIRED RESTAURANT MODIFIER
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

    if energy < 4.0:
        drop_prob = min(drop_prob * 1.35, 0.95)
    if stress > 7.0:
        drop_prob = min(drop_prob * 1.2, 0.95)

    # REQUIRED RESTAURANT MODIFIER
    drop_prob = min(0.95, drop_prob * (1.0 + vol))

    drop_requested = 1 if random.random() < drop_prob else 0

    # ------------------------------------------------------------------
    # 4. OSM (Open Shift Market)
    # ------------------------------------------------------------------
    num_offers = random.choice([0, 0, 1, 1, 1, 2, 2, 3])

    accept_prob = sched["osm_offer_accept_prob"]

    if energy > 7.0:
        accept_prob = min(1.0, accept_prob * 1.15)
    if fairness < 5.0:
        accept_prob = max(0.0, accept_prob * 0.75)
    if stress > 7.0:
        accept_prob = max(0.0, accept_prob * 0.7)

    # REQUIRED RESTAURANT MODIFIER
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
