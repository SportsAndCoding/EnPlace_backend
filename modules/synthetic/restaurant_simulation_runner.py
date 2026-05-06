"""
modules/synthetic/restaurant_simulation_runner.py

Restaurant-level orchestration for the En Place synthetic staffing simulation.

ARCHITECTURE v4 — LIFE EVENT EXITS:

    Two independent exit paths per day per staff member:

    1. PERSONA EVOLUTION EXIT — from evolve_persona(). Driven by emotional
       state, tenure, persona type. Modified by EP exit_modifier.
       This is the PREVENTABLE turnover that En Place reduces.

    2. LIFE EVENT EXIT — flat daily probability representing unavoidable
       turnover: moving, school, family, career change, pregnancy, etc.
       NOT modified by EP. Applies equally pre and post adoption.
       Creates realistic post-EP floor (~27% annual) and ensures
       enough replacement hires for L2 (cliff survival) to be measurable.

    Life event check runs AFTER persona evolution. If persona evolution
    already triggered an exit, life event is skipped (can't quit twice).
"""

from __future__ import annotations

import collections
import hashlib
from typing import Dict, List, Any, Optional, Deque

from modules.synthetic.daily_emotion_simulator import simulate_daily_emotions
from modules.synthetic.daily_behavior import simulate_daily_behavior
from modules.synthetic.persona_evolution import evolve_persona
from modules.synthetic.personas import PERSONA_DEFINITIONS


def _deterministic_staff_id(organization_id: int, index: int) -> str:
    key = f"{organization_id}:{index}"
    return hashlib.sha1(key.encode()).hexdigest()


def _deterministic_random(staff_id: str, day_index: int, salt: str) -> float:
    """Deterministic float [0, 1) for life event checks."""
    seed = f"{staff_id}:{day_index}:{salt}"
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return (h % 1_000_000) / 1_000_000


def _choose_persona_deterministically(
    weights: Dict[str, float],
    organization_id: int,
    staff_index: int,
) -> str:
    total = sum(weights.values())
    if abs(total - 1.0) > 0.01:
        raise ValueError(f"persona_weights must sum to ~1.0, got {total:.6f}")

    seed_key = f"{organization_id}:{staff_index}:persona_seed"
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
    if not history:
        return {
            "mood": 3.0,
            "safe_rate": 0.5,
            "fair_rate": 0.5,
            "respected_rate": 0.5,
        }

    recent = list(history)[-window:]
    n = len(recent)

    return {
        "mood": sum(d["mood_emoji"] for d in recent) / n,
        "safe_rate": sum(1 for d in recent if d["felt_safe"]) / n,
        "fair_rate": sum(1 for d in recent if d["felt_fair"]) / n,
        "respected_rate": sum(1 for d in recent if d["felt_respected"]) / n,
    }


def _create_staff_state(
    staff_id: str,
    staff_index: int,
    start_persona: str,
    hire_day: int,
    hired_with_stable_hire: bool = False,
) -> Dict[str, Any]:
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
        "hired_with_stable_hire": hired_with_stable_hire,
    }


# Life event exit reasons (deterministically selected)
_LIFE_EVENT_REASONS = [
    "relocated - moving to another city",
    "going back to school",
    "family emergency - needed to leave workforce",
    "career change - left restaurant industry",
    "childcare responsibilities",
    "health issue - extended leave needed",
    "spouse/partner job relocation",
    "better opportunity elsewhere - poached",
    "seasonal worker - planned departure",
    "personal reasons - voluntary resignation",
]


def simulate_restaurant(
    organization_id: int,
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
    if number_of_staff < 1:
        raise ValueError("number_of_staff must be >= 1")
    if simulation_days < 1:
        raise ValueError("simulation_days must be >= 1")

    # ------------------------------------------------------------------
    # EP effect setup
    # ------------------------------------------------------------------
    _get_daily_effect = None
    _stable_hire_weights = None
    _adoption_day = None
    _life_event_prob = 0.0  # Default: no life events unless configured

    if en_place_config is not None:
        from modules.synthetic.en_place_effect import get_daily_effect
        _get_daily_effect = get_daily_effect
        _stable_hire_weights = en_place_config.get("stable_hire_weights")
        _adoption_day = en_place_config.get("adoption_day")
        _life_event_prob = en_place_config.get("life_event_daily_prob", 0.0)

    # ------------------------------------------------------------------
    # Contagion setup
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
        graph = StaffGraph(organization_id=organization_id)
        mood_buffer = {}

    # ------------------------------------------------------------------
    # Initialize day-0 staff
    # ------------------------------------------------------------------
    staff_state: Dict[str, Dict[str, Any]] = {}
    active_staff_ids: List[str] = []
    next_staff_index: int = number_of_staff

    for i in range(number_of_staff):
        staff_id = _deterministic_staff_id(organization_id, i)
        start_persona = _choose_persona_deterministically(
            weights=persona_weights,
            organization_id=organization_id,
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

        # === STEP 0: EP effect for today ===
        ep_exit_modifier = 1.0
        ep_emotional_offset = None
        ep_active_today = False

        if _get_daily_effect is not None and en_place_config is not None:
            daily_effect = _get_daily_effect(en_place_config, day_index)
            ep_exit_modifier = daily_effect["exit_modifier"]
            ep_emotional_offset = daily_effect["emotional_offset"] or None
            ep_active_today = daily_effect["en_place_active"]

        # === STEP 1: Emotions ===
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

        # === STEP 2: Contagion ===
        if enable_contagion and graph is not None:
            todays_emotions, mood_buffer = apply_mood_contagion(
                staff_emotions=todays_emotions,
                graph=graph,
                contagion_strength=0.12,
                day_index=day_index,
                mood_buffer=mood_buffer,
            )

        # === STEP 3: Behaviors ===
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

        # === STEP 4: Pairwise events + graph ===
        if enable_contagion and graph is not None:
            pairwise_events = generate_pairwise_events(
                day_index=day_index,
                active_staff_ids=active_staff_ids,
                daily_behaviors=todays_behaviors,
                daily_emotions=todays_emotions,
                organization_id=organization_id,
                restaurant_profile=restaurant_profile,
            )
            graph.update_daily(day_index, pairwise_events)

        # === STEP 5: Persona evolution + Life events ===
        todays_exits: List[Dict[str, Any]] = []

        for sid in active_staff_ids:
            state = staff_state[sid]

            state["emotion_history"].append(todays_emotions[sid].copy())
            rolling = _compute_rolling_averages(state["emotion_history"])

            contagion_modifier = shock_modifiers.get(sid, 1.0)
            combined_modifier = ep_exit_modifier * contagion_modifier

            # --- EXIT PATH 1: Persona evolution (preventable) ---
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
            exit_this_day = False
            exit_reason = None

            if evolution["changed"] and new_persona == "exit":
                exit_this_day = True
                exit_reason = evolution["reason"]
            elif evolution["changed"]:
                state["current_persona"] = new_persona

            # --- EXIT PATH 2: Life event (unavoidable) ---
            # Only if not already exiting from persona evolution
            if not exit_this_day and _life_event_prob > 0:
                roll = _deterministic_random(sid, day_index, "life_event")
                if roll < _life_event_prob:
                    exit_this_day = True
                    # Deterministic reason selection
                    reason_idx = int(_deterministic_random(sid, day_index, "life_reason") * len(_LIFE_EVENT_REASONS))
                    reason_idx = min(reason_idx, len(_LIFE_EVENT_REASONS) - 1)
                    exit_reason = _LIFE_EVENT_REASONS[reason_idx]

            # --- Record outputs ---
            base = {
                "staff_id": sid,
                "organization_id": organization_id,
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

            if exit_this_day:
                state["exited"] = True
                state["exit_day"] = day_index + 1
                state["exit_reason"] = exit_reason
                state["current_persona"] = "exit"
                state["final_persona"] = "exit"
                state["en_place_active_on_exit"] = ep_active_today
                todays_exits.append({
                    "staff_id": sid,
                    "exit_reason": exit_reason,
                    "day_index": day_index,
                })
            else:
                state["final_persona"] = state["current_persona"]

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

        # === STEP 6: Exit processing (contagion) ===
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

        # Remove exited staff
        active_staff_ids = [
            sid for sid in active_staff_ids
            if not staff_state[sid]["exited"]
        ]

        # === STEP 6.5: Replacement hiring ===
        if enable_replacement_hiring and todays_exits:
            for _ in todays_exits:
                new_index = next_staff_index
                next_staff_index += 1

                new_id = _deterministic_staff_id(organization_id, new_index)

                use_stable_hire = (
                    ep_active_today
                    and _stable_hire_weights is not None
                )
                hire_weights = _stable_hire_weights if use_stable_hire else persona_weights

                new_persona = _choose_persona_deterministically(
                    weights=hire_weights,
                    organization_id=organization_id,
                    staff_index=new_index,
                )

                new_state = _create_staff_state(
                    staff_id=new_id,
                    staff_index=new_index,
                    start_persona=new_persona,
                    hire_day=day_index + 1,
                    hired_with_stable_hire=use_stable_hire,
                )
                staff_state[new_id] = new_state
                active_staff_ids.append(new_id)

                if graph is not None:
                    graph.add_node(new_id, new_persona)

        # === STEP 7: Decay shocks ===
        if enable_contagion and shock_modifiers:
            shock_modifiers = decay_shock_modifiers(shock_modifiers)

        # === STEP 8: Graph snapshot ===
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
    # Build staff master
    # ------------------------------------------------------------------
    staff_master: List[Dict[str, Any]] = []

    for sid, state in staff_state.items():
        staff_master.append({
            "staff_id": sid,
            "organization_id": organization_id,
            "start_persona": state["start_persona"],
            "final_persona": state["final_persona"],
            "total_days": state["tenure_days"],
            "hire_day": state["hire_day"],
            "exit_day": state["exit_day"],
            "exit_reason": state.get("exit_reason"),
            "en_place_active_on_exit": state.get("en_place_active_on_exit"),
            "hired_with_stable_hire": state.get("hired_with_stable_hire", False),
        })

    return {
        "staff_master": staff_master,
        "daily_emotions": daily_emotions,
        "daily_behavior": daily_behavior,
        "graph_snapshots": graph_snapshots,
        "exit_cascades": exit_cascades,
    }