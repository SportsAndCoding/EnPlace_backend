"""
modules/organic/pairwise_events_from_organic.py

Converts organic restaurant data (shifts, swaps, check-ins, open shift
volunteers) into pairwise events that feed the social graph engine.

This is the Tier 2 bridge: when a real restaurant goes live, their
organic data feeds the same StaffGraph that the synthetic simulation uses.

INPUT: Supabase queries against live tables for a specific restaurant + date.
OUTPUT: List[dict] with the same schema as synthetic pairwise_events.py:
    {
        "source_id": str,       # staff_id of one party
        "target_id": str,       # staff_id of the other
        "event_type": str,      # shift_cowork | swap_pickup | osm_pickup | mood_sync
        "weight": float,        # edge weight increment
        "day_date": str,        # ISO date string
    }

The nightly pipeline calls generate_organic_pairwise_events() once per
restaurant per day, then feeds the result into graph.update_daily().

WEIGHTS: Same as synthetic module for consistency.
    shift_cowork:  0.02 per co-occurrence
    swap_pickup:   0.15 per completed swap
    osm_pickup:    0.08 per volunteer acceptance
    mood_sync:     0.05 * similarity (threshold 0.8)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import combinations
from typing import Dict, List, Any, Optional

from database.supabase_client import supabase


# Edge weights — match synthetic module exactly
WEIGHT_SHIFT_COWORK = 0.02
WEIGHT_SWAP_PICKUP = 0.15
WEIGHT_OSM_PICKUP = 0.08
WEIGHT_MOOD_SYNC_BASE = 0.05
MOOD_SYNC_THRESHOLD = 0.8  # similarity must be >= this


def generate_organic_pairwise_events(
    organization_id: int,
    target_date: date,
) -> List[Dict[str, Any]]:
    """
    Generate pairwise events from one day of organic data for one restaurant.

    Queries four data sources and produces a unified list of pairwise events.
    Events from all sources share the same schema and can be fed directly
    into StaffGraph.update_daily().

    Parameters
    ----------
    organization_id : int
    target_date : date
        The date to pull events for.

    Returns
    -------
    list[dict]
        Pairwise events with keys: source_id, target_id, event_type,
        weight, day_date.
    """
    date_str = target_date.isoformat()
    events: List[Dict[str, Any]] = []

    # 1. Shift co-occurrence
    cowork_events = _generate_shift_cowork_events(organization_id, target_date, date_str)
    events.extend(cowork_events)

    # 2. Shift swaps
    swap_events = _generate_swap_events(organization_id, target_date, date_str)
    events.extend(swap_events)

    # 3. Open shift volunteers
    osm_events = _generate_osm_events(organization_id, target_date, date_str)
    events.extend(osm_events)

    # 4. Mood sync from check-ins
    mood_events = _generate_mood_sync_events(organization_id, target_date, date_str)
    events.extend(mood_events)

    return events


# ------------------------------------------------------------------
# 1. SHIFT CO-OCCURRENCE
# ------------------------------------------------------------------

def _generate_shift_cowork_events(
    organization_id: int,
    target_date: date,
    date_str: str,
) -> List[Dict[str, Any]]:
    """
    Two staff working the same shift date with overlapping scheduled times
    produce a shift_cowork event.

    Groups shifts by overlapping time windows rather than exact match,
    because a 6am-2pm and a 10am-6pm overlap for 4 hours — those people
    worked together.
    """
    result = supabase.table("sse_shifts") \
        .select("staff_id, scheduled_start, scheduled_end") \
        .eq("organization_id", organization_id) \
        .eq("shift_date", date_str) \
        .eq("status", "assigned") \
        .not_.is_("staff_id", "null") \
        .execute()

    if not result.data or len(result.data) < 2:
        return []

    # Parse shift times
    shifts = []
    for row in result.data:
        try:
            start = datetime.fromisoformat(row["scheduled_start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(row["scheduled_end"].replace("Z", "+00:00"))
            shifts.append({
                "staff_id": row["staff_id"],
                "start": start,
                "end": end,
            })
        except (ValueError, TypeError):
            continue

    # Generate cowork events for all overlapping pairs
    events = []
    for a, b in combinations(shifts, 2):
        if a["staff_id"] == b["staff_id"]:
            continue

        # Check overlap: A starts before B ends AND B starts before A ends
        overlap_start = max(a["start"], b["start"])
        overlap_end = min(a["end"], b["end"])

        if overlap_start < overlap_end:
            events.append({
                "source_id": a["staff_id"],
                "target_id": b["staff_id"],
                "event_type": "shift_cowork",
                "weight": WEIGHT_SHIFT_COWORK,
                "day_date": date_str,
            })

    return events


# ------------------------------------------------------------------
# 2. SHIFT SWAPS
# ------------------------------------------------------------------

def _generate_swap_events(
    organization_id: int,
    target_date: date,
    date_str: str,
) -> List[Dict[str, Any]]:
    """
    Completed shift swaps produce a swap_pickup event.
    The target_staff_id did a favor for requesting_staff_id.

    Directed: target -> requesting (the picker helped the requester).
    """
    result = supabase.table("shift_swaps") \
        .select("requesting_staff_id, target_staff_id") \
        .eq("organization_id", organization_id) \
        .eq("status", "approved") \
        .gte("created_at", f"{date_str}T00:00:00") \
        .lt("created_at", f"{(target_date + timedelta(days=1)).isoformat()}T00:00:00") \
        .not_.is_("target_staff_id", "null") \
        .execute()

    if not result.data:
        return []

    events = []
    for row in result.data:
        requester = row["requesting_staff_id"]
        picker = row["target_staff_id"]
        if requester and picker and requester != picker:
            events.append({
                "source_id": picker,
                "target_id": requester,
                "event_type": "swap_pickup",
                "weight": WEIGHT_SWAP_PICKUP,
                "day_date": date_str,
            })

    return events


# ------------------------------------------------------------------
# 3. OPEN SHIFT VOLUNTEERS
# ------------------------------------------------------------------

def _generate_osm_events(
    organization_id: int,
    target_date: date,
    date_str: str,
) -> List[Dict[str, Any]]:
    """
    When a staff member volunteers for an open shift and is accepted,
    they generate osm_pickup events with everyone already working
    that shift's date.

    Step 1: Find accepted volunteers for this date.
    Step 2: Find all other staff working on the same date.
    Step 3: Generate cowork events between volunteer and existing staff.
    """
    # Find accepted volunteers for shifts on this date
    vol_result = supabase.table("open_shift_volunteers") \
        .select("staff_id, open_shift_id") \
        .eq("organization_id", organization_id) \
        .eq("status", "accepted") \
        .gte("created_at", f"{date_str}T00:00:00") \
        .lt("created_at", f"{(target_date + timedelta(days=1)).isoformat()}T00:00:00") \
        .execute()

    if not vol_result.data:
        return []

    volunteer_staff_ids = {row["staff_id"] for row in vol_result.data if row["staff_id"]}

    if not volunteer_staff_ids:
        return []

    # Get all staff working this date (from sse_shifts)
    shift_result = supabase.table("sse_shifts") \
        .select("staff_id") \
        .eq("organization_id", organization_id) \
        .eq("shift_date", date_str) \
        .eq("status", "assigned") \
        .not_.is_("staff_id", "null") \
        .execute()

    working_staff_ids = {
        row["staff_id"] for row in (shift_result.data or [])
        if row["staff_id"]
    }

    events = []
    for vol_id in volunteer_staff_ids:
        for worker_id in working_staff_ids:
            if vol_id == worker_id:
                continue
            events.append({
                "source_id": vol_id,
                "target_id": worker_id,
                "event_type": "osm_pickup",
                "weight": WEIGHT_OSM_PICKUP,
                "day_date": date_str,
            })

    return events


# ------------------------------------------------------------------
# 4. MOOD SYNC
# ------------------------------------------------------------------

def _generate_mood_sync_events(
    organization_id: int,
    target_date: date,
    date_str: str,
) -> List[Dict[str, Any]]:
    """
    Staff who check in with similar moods on the same day produce
    mood_sync events. This captures emotional alignment — shared
    good days or shared bad days both strengthen bonds.

    Similarity = 1.0 - (|mood_a - mood_b| / 4.0) on a 1-5 scale.
    Threshold: 0.8 (moods within 1 point of each other).
    Weight: 0.05 * similarity.
    """
    result = supabase.table("sse_daily_checkins") \
        .select("staff_id, mood_emoji") \
        .eq("organization_id", organization_id) \
        .eq("checkin_date", date_str) \
        .not_.is_("mood_emoji", "null") \
        .execute()

    if not result.data or len(result.data) < 2:
        return []

    # Deduplicate: one check-in per staff per day (take latest / any)
    staff_moods: Dict[str, int] = {}
    for row in result.data:
        if row["staff_id"] and row["mood_emoji"] is not None:
            staff_moods[row["staff_id"]] = row["mood_emoji"]

    if len(staff_moods) < 2:
        return []

    events = []
    staff_list = list(staff_moods.items())

    for i, (sid_a, mood_a) in enumerate(staff_list):
        for sid_b, mood_b in staff_list[i + 1:]:
            similarity = 1.0 - (abs(mood_a - mood_b) / 4.0)

            if similarity >= MOOD_SYNC_THRESHOLD:
                weight = WEIGHT_MOOD_SYNC_BASE * similarity
                events.append({
                    "source_id": sid_a,
                    "target_id": sid_b,
                    "event_type": "mood_sync",
                    "weight": round(weight, 4),
                    "day_date": date_str,
                })

    return events