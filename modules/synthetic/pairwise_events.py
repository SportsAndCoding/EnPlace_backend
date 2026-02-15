"""
modules/synthetic/pairwise_events.py

Converts individual-level behavioral events into pairwise staff interactions
for the social graph engine.

When staff A has a swap approved, this module deterministically assigns
staff B as the counterparty from the active roster. When staff work the
same daypart, they generate shift co-occurrence edges. When two staff
members' moods track closely, they generate mood-sync edges.

All randomness is deterministic via hashlib (same pattern as every other
module in the pipeline). Given the same inputs, this module produces
identical outputs every time.

INPUT CONTRACTS (from existing modules):
    daily_behaviors[staff_id] keys:
        call_out: bool
        no_call_no_show: bool
        swap_requested: int (0 or 1)
        swap_approved: int (0 or 1)
        drop_requested: int (0 or 1)
        osm_offers_accepted: int (0+)
        osm_offers_declined: int (0+)

    daily_emotions[staff_id] keys:
        mood_emoji: int (1-5)
        felt_safe: bool
        felt_fair: bool
        felt_respected: bool

    restaurant_profile keys used:
        rush_curve: list[float]  (5 daypart intensities, 0-1)
        crew_cohesion: float (0-1)
        swap_culture: float (0-1)
"""

import hashlib
from typing import Dict, List, Any


# ---------------------------------------------------------------------------
# Deterministic randomness helpers (same pattern as persona_evolution.py)
# ---------------------------------------------------------------------------

def _det_float(restaurant_id: int, day_index: int, salt: str) -> float:
    """Deterministic float in [0, 1) from restaurant_id + day + salt."""
    seed_str = f"{restaurant_id}:{day_index}:{salt}"
    hash_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    return (hash_val % 1_000_000) / 1_000_000


def _det_shuffle(items: list, restaurant_id: int, day_index: int, salt: str) -> list:
    """
    Deterministic Fisher-Yates shuffle.
    Returns a new list; original is not mutated.
    """
    result = list(items)
    n = len(result)
    for i in range(n - 1, 0, -1):
        seed_str = f"{restaurant_id}:{day_index}:{salt}:shuffle:{i}"
        hash_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
        j = hash_val % (i + 1)
        result[i], result[j] = result[j], result[i]
    return result


def _det_weighted_choice(
    candidates: list,
    weights: list,
    restaurant_id: int,
    day_index: int,
    salt: str,
) -> Any:
    """
    Deterministic weighted random selection from candidates.
    weights must be same length as candidates and sum > 0.
    """
    total = sum(weights)
    if total <= 0 or not candidates:
        return None
    threshold = _det_float(restaurant_id, day_index, salt) * total
    cumulative = 0.0
    for candidate, weight in zip(candidates, weights):
        cumulative += weight
        if threshold < cumulative:
            return candidate
    return candidates[-1]  # numerical safety


# ---------------------------------------------------------------------------
# Presence determination
# ---------------------------------------------------------------------------

def _get_present_staff(
    active_staff_ids: List[str],
    daily_behaviors: Dict[str, Dict],
) -> List[str]:
    """
    Staff member is present if they did NOT call out and did NOT NCNS.
    Staff who called out or NCNS'd are absent for cowork events,
    but they still participate in swap events (someone covered for them).
    """
    present = []
    for sid in active_staff_ids:
        beh = daily_behaviors.get(sid)
        if beh is None:
            continue
        if beh.get("call_out", False) or beh.get("no_call_no_show", False):
            continue
        present.append(sid)
    return present


# ---------------------------------------------------------------------------
# Shift group assignment
# ---------------------------------------------------------------------------

def _assign_shift_groups(
    present_staff: List[str],
    restaurant_profile: Dict[str, Any],
    restaurant_id: int,
    day_index: int,
) -> Dict[str, List[str]]:
    """
    Assign present staff to daypart groups based on rush_curve.

    Uses the restaurant's rush_curve to determine relative staffing
    density across dayparts. Staff are deterministically shuffled and
    distributed proportionally.

    Returns:
        {"daypart_0": [staff_ids], "daypart_1": [...], ...}

    Doubles logic: ~15-25% of staff appear in two adjacent dayparts,
    creating natural bridge relationships across shifts.
    """
    rush_curve = restaurant_profile.get("rush_curve", [0.5, 0.7, 0.9, 0.8, 0.6])
    cohesion = restaurant_profile.get("crew_cohesion", 0.7)

    n_present = len(present_staff)
    if n_present < 2:
        return {"daypart_0": list(present_staff)}

    # Collapse to 2-3 meaningful dayparts based on rush_curve shape
    # Peak detection: find the dominant shift pattern
    peak_idx = rush_curve.index(max(rush_curve))

    if n_present <= 6:
        # Small crew: single shift, everyone works together
        return {"daypart_0": list(present_staff)}

    if n_present <= 12:
        # Medium crew: 2 dayparts (AM / PM split)
        split_point = len(rush_curve) // 2
        am_weight = sum(rush_curve[:split_point])
        pm_weight = sum(rush_curve[split_point:])
        total_weight = am_weight + pm_weight

        am_count = max(2, round(n_present * (am_weight / total_weight)))
        am_count = min(am_count, n_present - 2)  # ensure PM gets at least 2

        shuffled = _det_shuffle(present_staff, restaurant_id, day_index, "shift_assign")
        am_staff = shuffled[:am_count]
        pm_staff = shuffled[am_count:]

        # Doubles: some staff bridge both shifts
        doubles_rate = 0.15 + cohesion * 0.10  # 15-25% based on cohesion
        n_doubles = max(0, round(n_present * doubles_rate))
        n_doubles = min(n_doubles, min(len(am_staff), len(pm_staff)))

        if n_doubles > 0:
            # Last N from AM also appear in PM (they work doubles)
            doubles = am_staff[-n_doubles:]
            pm_staff = doubles + pm_staff

        return {"daypart_0": am_staff, "daypart_1": pm_staff}

    else:
        # Large crew: 3 dayparts (morning / mid / evening)
        weights = [
            sum(rush_curve[:2]),   # morning
            sum(rush_curve[1:4]),  # mid (overlaps intentionally)
            sum(rush_curve[3:]),   # evening
        ]
        total_w = sum(weights)
        counts = [max(2, round(n_present * (w / total_w))) for w in weights]

        # Adjust to not exceed n_present (allowing some overlap for doubles)
        shuffled = _det_shuffle(present_staff, restaurant_id, day_index, "shift_assign_3")

        morning = shuffled[: counts[0]]
        mid_start = counts[0] - 1  # 1 person overlaps morning/mid
        mid_end = mid_start + counts[1]
        mid = shuffled[mid_start: min(mid_end, n_present)]
        eve_start = mid_end - 1  # 1 person overlaps mid/evening
        evening = shuffled[eve_start: min(eve_start + counts[2], n_present)]

        # Catch anyone not assigned (rounding edge case)
        assigned = set(morning + mid + evening)
        unassigned = [s for s in present_staff if s not in assigned]
        if unassigned:
            evening.extend(unassigned)

        groups = {"daypart_0": morning, "daypart_1": mid, "daypart_2": evening}
        # Remove empty groups
        return {k: v for k, v in groups.items() if v}


# ---------------------------------------------------------------------------
# Event generators
# ---------------------------------------------------------------------------

def _generate_cowork_events(
    shift_groups: Dict[str, List[str]],
    day_index: int,
) -> List[Dict[str, Any]]:
    """
    Generate shift_cowork events for all pairs within each daypart group.

    Every pair of staff in the same shift group gets one cowork event.
    Weight is 0.02 (low per event, but accumulates daily).
    """
    events = []
    for daypart, staff_ids in shift_groups.items():
        n = len(staff_ids)
        for i in range(n):
            for j in range(i + 1, n):
                events.append({
                    "day_index": day_index,
                    "event_type": "shift_cowork",
                    "source_id": staff_ids[i],
                    "target_id": staff_ids[j],
                    "weight": 0.02,
                    "direction": "undirected",
                    "context": daypart,
                })
    return events


def _generate_swap_events(
    day_index: int,
    active_staff_ids: List[str],
    daily_behaviors: Dict[str, Dict],
    daily_emotions: Dict[str, Dict],
    present_staff: List[str],
    restaurant_id: int,
) -> List[Dict[str, Any]]:
    """
    When staff A has swap_approved == 1, assign a counterparty B.

    Counterparty selection priority:
    1. Present staff who accepted OSM offers today (proven willing helpers)
    2. Present staff with mood >= 4 (happy people help more)
    3. Random present staff (fallback)

    The swap requester does NOT need to be present (they could be
    swapping away a future shift). The pickup person IS present.

    Directed: B -> A (B did A a favor).
    """
    events = []

    # Find staff who had swaps approved
    swap_requesters = [
        sid for sid in active_staff_ids
        if daily_behaviors.get(sid, {}).get("swap_approved", 0) == 1
    ]

    if not swap_requesters or len(present_staff) < 2:
        return events

    for req_idx, requester_id in enumerate(swap_requesters):
        # Build candidate pool (present staff excluding the requester)
        candidates = [s for s in present_staff if s != requester_id]
        if not candidates:
            continue

        # Weight candidates: OSM acceptors and happy staff preferred
        weights = []
        for cid in candidates:
            w = 1.0  # base weight
            beh = daily_behaviors.get(cid, {})
            emo = daily_emotions.get(cid, {})

            # OSM acceptors are proven willing helpers
            if beh.get("osm_offers_accepted", 0) >= 1:
                w += 3.0

            # Happy staff more likely to help
            mood = emo.get("mood_emoji", 3)
            if mood >= 4:
                w += 1.5
            elif mood >= 3:
                w += 0.5

            # Felt respected = more generous
            if emo.get("felt_respected", False):
                w += 0.5

            weights.append(w)

        picker = _det_weighted_choice(
            candidates, weights,
            restaurant_id, day_index,
            salt=f"swap_assign:{requester_id}:{req_idx}",
        )

        if picker is not None:
            events.append({
                "day_index": day_index,
                "event_type": "swap_pickup",
                "source_id": picker,          # B picked up
                "target_id": requester_id,     # A's shift
                "weight": 0.15,
                "direction": "directed",
                "context": "swap_approved",
            })

    return events


def _generate_osm_events(
    day_index: int,
    daily_behaviors: Dict[str, Dict],
    present_staff: List[str],
    shift_groups: Dict[str, List[str]],
    restaurant_id: int,
) -> List[Dict[str, Any]]:
    """
    When staff B accepts an OSM offer, they're working an extra shift.
    This creates co-occurrence with whoever is in that daypart.

    Since we don't know which specific open shift was picked up,
    we deterministically assign the OSM acceptor to a daypart and
    generate cowork events with the existing staff in that group.

    Weight: 0.08 per OSM-driven co-occurrence (higher than normal
    cowork because the OSM acceptor chose to be there — signals
    investment in the team).
    """
    events = []

    # Find staff who accepted OSM offers
    osm_acceptors = [
        sid for sid in present_staff
        if daily_behaviors.get(sid, {}).get("osm_offers_accepted", 0) >= 1
    ]

    if not osm_acceptors or not shift_groups:
        return events

    daypart_keys = list(shift_groups.keys())

    for acc_idx, acceptor_id in enumerate(osm_acceptors):
        # Assign to a daypart they're NOT already in
        # (OSM means picking up an EXTRA shift, likely a different daypart)
        home_dayparts = [
            dp for dp, members in shift_groups.items()
            if acceptor_id in members
        ]
        other_dayparts = [dp for dp in daypart_keys if dp not in home_dayparts]

        if other_dayparts:
            # Deterministically pick which extra daypart they worked
            dp_idx_hash = _det_float(
                restaurant_id, day_index,
                f"osm_daypart:{acceptor_id}:{acc_idx}"
            )
            target_dp = other_dayparts[int(dp_idx_hash * len(other_dayparts))]
        elif daypart_keys:
            # Only one daypart exists — they doubled up in the same one
            target_dp = daypart_keys[0]
        else:
            continue

        # Generate cowork events with everyone in that daypart
        for coworker_id in shift_groups.get(target_dp, []):
            if coworker_id == acceptor_id:
                continue
            events.append({
                "day_index": day_index,
                "event_type": "osm_pickup",
                "source_id": acceptor_id,
                "target_id": coworker_id,
                "weight": 0.08,
                "direction": "undirected",
                "context": f"osm_into_{target_dp}",
            })

    return events


def _generate_mood_sync_events(
    day_index: int,
    present_staff: List[str],
    daily_emotions: Dict[str, Dict],
    restaurant_id: int,
    sync_threshold: float = 0.8,
) -> List[Dict[str, Any]]:
    """
    Detect pairs of staff with similar emotional states today.

    Mood sync is computed as proximity on the 1-5 scale:
        similarity = 1.0 - (|mood_a - mood_b| / 4.0)

    Two staff with identical moods get similarity=1.0.
    Two staff at opposite extremes (1 vs 5) get similarity=0.0.

    Only pairs above sync_threshold generate events (default 0.8,
    meaning moods differ by at most 1 point on the 5-point scale).

    Additionally, shared boolean states amplify the sync signal:
        +0.1 if both felt_safe matches
        +0.1 if both felt_fair matches
        +0.1 if both felt_respected matches

    Weight: 0.05 * final_similarity (capped at 0.065)

    NOTE: This is a daily proximity measure. A future enhancement
    could compute rolling correlation across a multi-day window for
    stronger temporal coupling detection. The graph's edge accumulation
    and decay naturally builds this over time — two people who are
    mood-synced every day will accumulate a strong mood_sync edge
    even from daily snapshots.
    """
    events = []

    # Only compute for reasonably sized groups
    # For 30+ present staff, O(n^2) pairs = 435+. We cap by only
    # evaluating pairs that are already in the same shift group
    # OR by sampling. For now, evaluate all pairs — restaurant staff
    # counts rarely exceed 30 present on a given day.
    if len(present_staff) < 2:
        return events

    for i in range(len(present_staff)):
        sid_a = present_staff[i]
        emo_a = daily_emotions.get(sid_a)
        if emo_a is None:
            continue
        mood_a = emo_a.get("mood_emoji", 3)

        for j in range(i + 1, len(present_staff)):
            sid_b = present_staff[j]
            emo_b = daily_emotions.get(sid_b)
            if emo_b is None:
                continue
            mood_b = emo_b.get("mood_emoji", 3)

            # Base similarity from mood proximity
            similarity = 1.0 - (abs(mood_a - mood_b) / 4.0)

            if similarity < sync_threshold:
                continue

            # Boolean alignment bonus
            if emo_a.get("felt_safe") == emo_b.get("felt_safe"):
                similarity += 0.1
            if emo_a.get("felt_fair") == emo_b.get("felt_fair"):
                similarity += 0.1
            if emo_a.get("felt_respected") == emo_b.get("felt_respected"):
                similarity += 0.1

            similarity = min(1.3, similarity)  # cap with bonuses

            weight = 0.05 * similarity
            weight = min(0.065, weight)  # hard cap per interface spec

            events.append({
                "day_index": day_index,
                "event_type": "mood_sync",
                "source_id": sid_a,
                "target_id": sid_b,
                "weight": round(weight, 4),
                "direction": "undirected",
                "context": f"similarity_{similarity:.2f}",
            })

    return events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_pairwise_events(
    *,
    day_index: int,
    active_staff_ids: List[str],
    daily_behaviors: Dict[str, Dict],
    daily_emotions: Dict[str, Dict],
    restaurant_id: int,
    restaurant_profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Convert individual behavior events into pairwise staff interactions
    for the social graph engine.

    Parameters
    ----------
    day_index : int
        Current simulation day (0-based).
    active_staff_ids : list[str]
        All staff IDs still employed (not yet exited).
    daily_behaviors : dict[str, dict]
        {staff_id: behavior_dict} from daily_behavior.simulate_daily_behavior().
    daily_emotions : dict[str, dict]
        {staff_id: emotion_output_dict} from daily_emotion_simulator output.
    restaurant_id : int
        Restaurant identifier for deterministic randomness.
    restaurant_profile : dict
        Restaurant configuration (needs rush_curve, crew_cohesion, swap_culture).

    Returns
    -------
    list[dict]
        Each event dict contains:
            day_index: int
            event_type: str   ("shift_cowork" | "swap_pickup" | "osm_pickup" | "mood_sync")
            source_id: str
            target_id: str
            weight: float     (interaction strength for graph edge update)
            direction: str    ("directed" | "undirected")
            context: str      (metadata for debugging/analysis)

    Event type summary:
        shift_cowork:  Same daypart, weight 0.02  (frequent, weak signal)
        swap_pickup:   B covered A's swap, weight 0.15  (rare, strong signal)
        osm_pickup:    B took open shift into a daypart, weight 0.08  (moderate)
        mood_sync:     Emotional proximity today, weight 0.05 * sim  (continuous, moderate)
    """
    if len(active_staff_ids) < 2:
        return []

    # Step 1: Determine who is physically present today
    present_staff = _get_present_staff(active_staff_ids, daily_behaviors)

    if len(present_staff) < 2:
        return []

    # Step 2: Assign present staff to shift daypart groups
    shift_groups = _assign_shift_groups(
        present_staff, restaurant_profile, restaurant_id, day_index,
    )

    # Step 3: Generate all event types
    events = []

    # 3a. Shift co-occurrence (pairs within same daypart)
    events.extend(
        _generate_cowork_events(shift_groups, day_index)
    )

    # 3b. Swap pickups (directed: helper -> requester)
    events.extend(
        _generate_swap_events(
            day_index, active_staff_ids, daily_behaviors,
            daily_emotions, present_staff, restaurant_id,
        )
    )

    # 3c. OSM pickups (acceptor co-occurs with extra daypart)
    events.extend(
        _generate_osm_events(
            day_index, daily_behaviors, present_staff,
            shift_groups, restaurant_id,
        )
    )

    # 3d. Mood sync (emotional proximity between present staff)
    events.extend(
        _generate_mood_sync_events(
            day_index, present_staff, daily_emotions, restaurant_id,
        )
    )

    return events