"""
modules/synthetic/restaurant_simulation_runner.py

Restaurant-level orchestration for the En Place synthetic staffing simulation.
Creates a deterministic cohort of staff, runs them through a DAY-SYNCHRONIZED
loop, and returns flattened tables for analysis.

ARCHITECTURE v3 — REPLACEMENT HIRING + EN PLACE EFFECT:

    When a staff member exits, a replacement is hired on the next day.
    The restaurant maintains its target headcount throughout the simulation,
    just like a real business. New hires get fresh personas, tenure=0, and
    benefit from (or suffer without) EP based on whether it's active that day.

    Combined with the En Place effect engine, this creates the before/after
    story: same restaurant, same environment, same manager — the only thing
    that changed is En Place activating on adoption_day.

    Each staff member tracks their own hire_day so turnover can be calculated
    per period rather than cumulatively.

OUTPUT TABLES:
    staff_master    -> one row per employee (including replacement hires)
    daily_emotions  -> one row per employee per simulated day
    daily_behavior  -> one row per employee per simulated day
    graph_snapshots -> one per snapshot_interval days (contagion only)
    exit_cascades   -> one per exit event with cascade analysis (contagion only)

DETERMINISM: All output is fully deterministic given the same inputs.
"""

from __future__ import annotations

import collections
import hashlib
from typing import Dict, List, Any, Optional, Deque

from modules.synthetic.daily_emotion_simulator import simulate_daily_emotions
from modules.synthetic.daily_behavior import simulate_daily_behavior
from modules.synthetic.persona_evolution import evolve_persona
from modules.synthetic.personas import PERSONA_DEFINITIONS


def _deterministic_staff_id(restaurant_id: int, index: int) -> str:
    """Generate a stable staff_id from restaurant_id and staff index."""
    key = f"{restaurant_id}:{index}"
    return hashlib.sha1(key.encode()).hexdigest()


def _choose_persona_deterministically(
    weights: Dict[str, float],
    restaurant_id: int,
    staff_index: int,
) -> str:
    """Select a starting persona using fully deterministic weighted choice."""
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"persona_weights must sum to ~1.0, got {total:.6f}")

    seed_key = f"{restaurant_id}:{staff_index}:persona_seed"
    seed_hash = hashlib.sha1(seed_key.encode()).hexdigest()
    seed_int = int(seed_hash, 16)
    offset = (seed_int % 1_000_000_000) / 1_000_000_000.0

    cumulative = 0.0
    for persona, weight in weights.items():
        if persona not in PERSONA_DEFINITIONS:
            raise ValueError(f"Unknown persona '{persona}' in persona_weights")
        cumulative += weight / total
        if offset < cumulative:
            return persona

    return next(iter(PERSONA_DEFINITIONS))


def _compute_rolling_averages(
    history: Deque[Dict[str, Any]],
    window: int = 30,
) -> Dict[str, float]:
    """Compute rolling averages from emotion history."""
    if not history:
        return {
            "mood": 3.0,
            "safe_rate": 0.5,
            "fair_rate": 0.5,
            "respected_rate": 0.5,
        }

    recent = list(history)[-window:]
    n = len(recent)

    mood_avg = sum(d["mood_emoji"] for d in recent) / n
    safe_rate = sum(1 for d in recent if d["felt_safe"]) / n
    fair_rate = sum(1 for d in recent if d["felt_fair"]) / n
    respected_rate = sum(1 for d in recent if d["felt_respected"]) / n

    return {
        "mood": mood_avg,
        "safe_rate": safe_rate,
        "fair_rate": fair_rate,
        "respected_rate": respected_rate,
    }


def _create_staff_state(
    staff_id: str,
    staff_index: int,
    start_persona: str,
    hire_day: int,
) -> Dict[str, Any]:
    """Create initial mutable state for a staff member."""
    return {
        "staff_id": staff_id,
        "staff_index": staff_index,
        "start_persona": start_persona,
        "current_persona": start_persona,
        "hire_day": hire_day,
        "tenure_days": 0,
        "previous_emotions": None,
        "emotion_history": collections.deque(maxlen=30),
        "exited": False,
        "exit_day": None,
        "exit_reason": None,
        "final_persona": start_persona,
        "en_place_active_on_exit": None,
    }


def simulate_restaurant(
    restaurant_id: int,
    number_of_staff: int,
    simulation_days: int,
    persona_weights: Dict[str, float],
    restaurant_profile: Dict[str, Any],
    *,
    enable_contagion: bool = False,
    graph_snapshot_interval: int = 7,
    en_place_config: Optional[Dict[str, Any]] = None,
    enable_replacement_hiring: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Simulate an entire restaurant's staffing history.

    Parameters
    ----------
    restaurant_id : int
        Unique identifier for the restaurant.
    number_of_staff : int
        Target headcount. With replacement hiring, maintained throughout sim.
    simulation_days : int
        Length of the simulation in days.
    persona_weights : Dict[str, float]
        Weighted distribution of starting personas.
    restaurant_profile : Dict[str, Any]
        Restaurant configuration affecting behavior patterns.
    enable_contagion : bool
        When True, activates social graph modules.
    graph_snapshot_interval : int
        Days between graph snapshots (only used when enable_contagion=True).
    en_place_config : dict or None
        Output from en_place_effect.get_en_place_config(). When None, no EP effect.
    enable_replacement_hiring : bool
        When True (default), exited staff are replaced next day to maintain
        target headcount. When False, headcount declines as staff exit.

    Returns
    -------
    dict with keys:
        "staff_master"     -> list[dict], one row per employee (incl replacements)
        "daily_emotions"   -> list[dict], one row per employee per day
        "daily_behavior"   -> list[dict], one row per employee per day
        "graph_snapshots"  -> list[dict], periodic graph state (contagion only)
        "exit_cascades"    -> list[dict], cascade analysis per exit (contagion only)
    """
    if number_of_staff < 1:
        raise ValueError("number_of_staff must be >= 1")
    if simulation_days < 1:
        raise ValueError("simulation_days must be >= 1")

    # ------------------------------------------------------------------
    # Import EP effect module if config provided
    # ------------------------------------------------------------------
    _get_daily_effect = None
    if en_place_config is not None:
        from modules.synthetic.en_place_effect import get_daily_effect
        _get_daily_effect = get_daily_effect

    # ------------------------------------------------------------------
    # Lazy imports for contagion modules
    # ------------------------------------------------------------------
    graph = None
    mood_buffer = None
    shock_modifiers: Dict[str, float] = {}

    if enable_contagion:
        from modules.synthetic.pairwise_events import generate_pairwise_events
        from modules.synthetic.social_graph import StaffGraph
        from modules.synthetic.contagion_engine import (
            apply_mood_contagion,
            apply_exit_shock,
            accumulate_shock_modifiers,
            decay_shock_modifiers,
        )

        graph = StaffGraph(restaurant_id=restaurant_id)
        mood_buffer = {}

    # ------------------------------------------------------------------
    # Initialize all staff (day-0 cohort)
    # ------------------------------------------------------------------
    staff_state: Dict[str, Dict[str, Any]] = {}
    active_staff_ids: List[str] = []
    next_staff_index: int = number_of_staff  # Counter for replacement hires

    for i in range(number_of_staff):
        staff_id = _deterministic_staff_id(restaurant_id, i)
        start_persona = _choose_persona_deterministically(
            weights=persona_weights,
            restaurant_id=restaurant_id,
            staff_index=i,
        )

        state = _create_staff_state(staff_id, i, start_persona, hire_day=0)
        staff_state[staff_id] = state
        active_staff_ids.append(staff_id)

        if graph is not None:
            graph.add_node(staff_id, start_persona)

    # ------------------------------------------------------------------
    # Output accumulators
    # ------------------------------------------------------------------
    daily_emotions: List[Dict[str, Any]] = []
    daily_behavior: List[Dict[str, Any]] = []
    graph_snapshots: List[Dict[str, Any]] = []
    exit_cascades: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # DAY-SYNCHRONIZED LOOP
    # ------------------------------------------------------------------
    for day_index in range(simulation_days):
        if not active_staff_ids:
            break

        # ==============================================================
        # STEP 0: Compute En Place effect for today
        # ==============================================================
        ep_exit_modifier = 1.0
        ep_emotional_offset = None
        ep_active_today = False

        if _get_daily_effect is not None and en_place_config is not None:
            daily_effect = _get_daily_effect(en_place_config, day_index)
            ep_exit_modifier = daily_effect["exit_modifier"]
            ep_emotional_offset = daily_effect["emotional_offset"] or None
            ep_active_today = daily_effect["en_place_active"]

        # ==============================================================
        # STEP 1: Compute emotions for all active staff
        # ==============================================================
        todays_emotions: Dict[str, Dict[str, Any]] = {}
        todays_emotion_results: Dict[str, Dict[str, Any]] = {}

        for sid in active_staff_ids:
            state = staff_state[sid]
            emotion_result = simulate_daily_emotions(
                persona_key=state["current_persona"],
                previous_emotions=state["previous_emotions"],
                day_index=day_index,
                staff_id=sid,
                emotional_offset=ep_emotional_offset,
            )
            todays_emotions[sid] = emotion_result["output"]
            todays_emotion_results[sid] = emotion_result

        # ==============================================================
        # STEP 2: Apply mood contagion (if enabled)
        # ==============================================================
        if enable_contagion and graph is not None:
            todays_emotions, mood_buffer = apply_mood_contagion(
                staff_emotions=todays_emotions,
                graph=graph,
                contagion_strength=0.12,
                day_index=day_index,
                mood_buffer=mood_buffer,
            )

        # ==============================================================
        # STEP 3: Compute behaviors for all active staff
        # ==============================================================
        todays_behaviors: Dict[str, Dict[str, Any]] = {}

        for sid in active_staff_ids:
            state = staff_state[sid]
            behavior = simulate_daily_behavior(
                staff_id=sid,
                persona_key=state["current_persona"],
                emotional_state=todays_emotions[sid],
                tenure_days=state["tenure_days"],
                day_index=day_index,
                restaurant_profile=restaurant_profile,
            )
            todays_behaviors[sid] = behavior

        # ==============================================================
        # STEP 4: Generate pairwise events & update graph (if enabled)
        # ==============================================================
        if enable_contagion and graph is not None:
            pairwise_events = generate_pairwise_events(
                day_index=day_index,
                active_staff_ids=active_staff_ids,
                daily_behaviors=todays_behaviors,
                daily_emotions=todays_emotions,
                restaurant_id=restaurant_id,
                restaurant_profile=restaurant_profile,
            )
            graph.update_daily(day_index, pairwise_events)

        # ==============================================================
        # STEP 5: Persona evolution for all active staff
        # ==============================================================
        todays_exits: List[Dict[str, Any]] = []

        for sid in active_staff_ids:
            state = staff_state[sid]

            state["emotion_history"].append(todays_emotions[sid].copy())
            rolling = _compute_rolling_averages(state["emotion_history"])

            contagion_modifier = shock_modifiers.get(sid, 1.0)
            combined_modifier = ep_exit_modifier * contagion_modifier

            evolution = evolve_persona(
                current_persona=state["current_persona"],
                tenure_days=state["tenure_days"],
                rolling_mood=rolling["mood"],
                rolling_safe_rate=rolling["safe_rate"],
                rolling_fair_rate=rolling["fair_rate"],
                rolling_respected_rate=rolling["respected_rate"],
                staff_id=sid,
                exit_probability_modifier=combined_modifier,
            )

            new_persona = evolution["new_persona"]
            reason = evolution["reason"]

            base = {
                "staff_id": sid,
                "restaurant_id": restaurant_id,
                "day_index": day_index,
                "tenure_days": state["tenure_days"],
            }

            daily_emotions.append({
                **base,
                "mood_emoji": todays_emotions[sid]["mood_emoji"],
                "felt_safe": todays_emotions[sid]["felt_safe"],
                "felt_fair": todays_emotions[sid]["felt_fair"],
                "felt_respected": todays_emotions[sid]["felt_respected"],
            })

            daily_behavior.append({
                **base,
                **todays_behaviors[sid],
            })

            if evolution["changed"]:
                state["current_persona"] = new_persona
                if new_persona == "exit":
                    state["exited"] = True
                    state["exit_day"] = day_index + 1
                    state["exit_reason"] = reason
                    state["final_persona"] = "exit"
                    state["en_place_active_on_exit"] = ep_active_today
                    todays_exits.append({
                        "staff_id": sid,
                        "exit_reason": reason,
                        "day_index": day_index,
                    })
            else:
                state["final_persona"] = new_persona

            if graph is not None:
                graph.update_node_state(
                    sid,
                    persona=state["current_persona"],
                    tenure_days=state["tenure_days"],
                    current_mood=todays_emotions[sid]["mood_emoji"],
                    rolling_mood=rolling["mood"],
                    rolling_safe_rate=rolling["safe_rate"],
                    rolling_fair_rate=rolling["fair_rate"],
                    rolling_respected_rate=rolling["respected_rate"],
                )

            state["previous_emotions"] = todays_emotion_results[sid]
            state["tenure_days"] += 1

        # ==============================================================
        # STEP 6: Process exits — cascade analysis & shock propagation
        # ==============================================================
        if enable_contagion and graph is not None and todays_exits:
            new_shocks: List[Dict[str, float]] = []

            for exit_info in todays_exits:
                sid = exit_info["staff_id"]

                cascade = graph.simulate_cascade(sid, iterations=100)
                exit_cascades.append({
                    "staff_id": sid,
                    "day_index": exit_info["day_index"],
                    "exit_reason": exit_info["exit_reason"],
                    "cascade_severity": cascade["cascade_severity"],
                    "expected_additional_exits": cascade["expected_additional_exits"],
                    "worst_case_exits": cascade["worst_case_exits"],
                    "at_risk_staff": cascade["at_risk_staff"][:5],
                })

                shock = apply_exit_shock(
                    exited_staff_id=sid,
                    exit_reason=exit_info["exit_reason"],
                    graph=graph,
                )
                new_shocks.append(shock)

                graph.remove_node(sid)

            if new_shocks:
                shock_modifiers = accumulate_shock_modifiers(
                    shock_modifiers, *new_shocks
                )

        # Remove exited staff from active list
        active_staff_ids = [
            sid for sid in active_staff_ids
            if not staff_state[sid]["exited"]
        ]

        # ==============================================================
        # STEP 6.5: Replacement hiring
        # For each exit today, hire a replacement starting tomorrow.
        # New hires get fresh personas and tenure=0.
        # ==============================================================
        if enable_replacement_hiring and todays_exits:
            for exit_info in todays_exits:
                new_index = next_staff_index
                next_staff_index += 1

                new_id = _deterministic_staff_id(restaurant_id, new_index)
                new_persona = _choose_persona_deterministically(
                    weights=persona_weights,
                    restaurant_id=restaurant_id,
                    staff_index=new_index,
                )

                new_state = _create_staff_state(
                    staff_id=new_id,
                    staff_index=new_index,
                    start_persona=new_persona,
                    hire_day=day_index + 1,
                )
                staff_state[new_id] = new_state
                active_staff_ids.append(new_id)

                if graph is not None:
                    graph.add_node(new_id, new_persona)

        # ==============================================================
        # STEP 7: Decay shock modifiers for next day
        # ==============================================================
        if enable_contagion and shock_modifiers:
            shock_modifiers = decay_shock_modifiers(shock_modifiers)

        # ==============================================================
        # STEP 8: Periodic graph snapshot
        # ==============================================================
        if (
            enable_contagion
            and graph is not None
            and day_index > 0
            and day_index % graph_snapshot_interval == 0
        ):
            graph.compute_centrality()
            snapshot = graph.get_graph_snapshot(day_index=day_index)
            graph_snapshots.append(snapshot)

    # ------------------------------------------------------------------
    # Build staff master records for ALL staff (original + replacements)
    # ------------------------------------------------------------------
    staff_master: List[Dict[str, Any]] = []

    for sid, state in staff_state.items():
        total_days = state["tenure_days"]
        staff_master.append({
            "staff_id": sid,
            "restaurant_id": restaurant_id,
            "start_persona": state["start_persona"],
            "final_persona": state["final_persona"],
            "total_days": total_days,
            "hire_day": state["hire_day"],
            "exit_day": state["exit_day"],
            "en_place_active_on_exit": state.get("en_place_active_on_exit"),
        })

    return {
        "staff_master": staff_master,
        "daily_emotions": daily_emotions,
        "daily_behavior": daily_behavior,
        "graph_snapshots": graph_snapshots,
        "exit_cascades": exit_cascades,
    }