"""
modules/synthetic/personas.py

Persona definitions for the En Place synthetic staffing simulator.

UPDATED: Schema now matches organic check-in data exactly:
- mood: 1-5 scale (integer, matches emoji picker)
- felt_safe: boolean
- felt_fair: boolean  
- felt_respected: boolean

Each persona defines:
- baseline: typical daily values (mood as 1-5, others as probabilities 0-1)
- volatility: how much daily values fluctuate
- inertia: how much yesterday affects today (0 = no memory, 1 = sticky)
- attendance_bias: probabilities for lateness, callouts, NCNS
- schedule_behavior: shift swap/drop/pickup tendencies
- sensitivities: how much unfairness/disrespect affects exit probability
"""

from typing import Dict, Any

PERSONA_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    
    # =========================================================================
    # ROOKIE STAGE (0-30 days typical)
    # =========================================================================
    
    "enthusiastic_rookie": {
        "name": "Enthusiastic Rookie",
        "stage": "rookie",
        "baseline": {
            "mood": 4.5,           # 1-5 scale, high mood
            "felt_safe_prob": 0.95,
            "felt_fair_prob": 0.90,
            "felt_respected_prob": 0.92,
        },
        "volatility": {
            "mood": 0.4,           # Can swing a bit day to day
            "felt_safe_prob": 0.05,
            "felt_fair_prob": 0.08,
            "felt_respected_prob": 0.06,
        },
        "inertia": {
            "mood": 0.4,
            "felt_safe_prob": 0.5,
            "felt_fair_prob": 0.5,
            "felt_respected_prob": 0.5,
        },
        "attendance_bias": {
            "late_prob": 0.05,
            "callout_prob": 0.02,
            "ncns_prob": 0.001
        },
        "schedule_behavior": {
            "swap_request_prob": 0.08,
            "drop_request_prob": 0.05,
            "osm_offer_accept_prob": 0.78,
            "osm_offer_decline_prob": 0.22
        },
        "fairness_sensitivity": 0.85,
        "respect_sensitivity": 0.80,
        "safety_sensitivity": 0.70,
        "description": "Eager new hire who loves the job and wants to prove themselves."
    },
    
    "lazy_rookie": {
        "name": "Lazy Rookie",
        "stage": "rookie",
        "baseline": {
            "mood": 3.0,
            "felt_safe_prob": 0.80,
            "felt_fair_prob": 0.60,
            "felt_respected_prob": 0.65,
        },
        "volatility": {
            "mood": 0.3,
            "felt_safe_prob": 0.08,
            "felt_fair_prob": 0.12,
            "felt_respected_prob": 0.10,
        },
        "inertia": {
            "mood": 0.5,
            "felt_safe_prob": 0.5,
            "felt_fair_prob": 0.6,
            "felt_respected_prob": 0.5,
        },
        "attendance_bias": {
            "late_prob": 0.30,
            "callout_prob": 0.15,
            "ncns_prob": 0.03
        },
        "schedule_behavior": {
            "swap_request_prob": 0.15,
            "drop_request_prob": 0.40,
            "osm_offer_accept_prob": 0.08,
            "osm_offer_decline_prob": 0.92
        },
        "fairness_sensitivity": 0.60,
        "respect_sensitivity": 0.50,
        "safety_sensitivity": 0.40,
        "description": "Does the bare minimum and shows up only when it suits them."
    },
    
    "snarky_rookie": {
        "name": "Snarky Rookie",
        "stage": "rookie",
        "baseline": {
            "mood": 3.2,
            "felt_safe_prob": 0.85,
            "felt_fair_prob": 0.50,
            "felt_respected_prob": 0.55,
        },
        "volatility": {
            "mood": 0.5,
            "felt_safe_prob": 0.06,
            "felt_fair_prob": 0.15,
            "felt_respected_prob": 0.12,
        },
        "inertia": {
            "mood": 0.4,
            "felt_safe_prob": 0.5,
            "felt_fair_prob": 0.5,
            "felt_respected_prob": 0.5,
        },
        "attendance_bias": {
            "late_prob": 0.20,
            "callout_prob": 0.08,
            "ncns_prob": 0.01
        },
        "schedule_behavior": {
            "swap_request_prob": 0.35,
            "drop_request_prob": 0.20,
            "osm_offer_accept_prob": 0.40,
            "osm_offer_decline_prob": 0.60
        },
        "fairness_sensitivity": 0.80,
        "respect_sensitivity": 0.85,
        "safety_sensitivity": 0.50,
        "description": "Quick-witted new hire with a sharp tongue - complains but still gets the job done."
    },
    
    "overwhelmed_rookie": {
        "name": "Overwhelmed Rookie",
        "stage": "rookie",
        "baseline": {
            "mood": 2.5,
            "felt_safe_prob": 0.65,
            "felt_fair_prob": 0.55,
            "felt_respected_prob": 0.60,
        },
        "volatility": {
            "mood": 0.6,
            "felt_safe_prob": 0.12,
            "felt_fair_prob": 0.15,
            "felt_respected_prob": 0.12,
        },
        "inertia": {
            "mood": 0.6,
            "felt_safe_prob": 0.7,
            "felt_fair_prob": 0.7,
            "felt_respected_prob": 0.7,
        },
        "attendance_bias": {
            "late_prob": 0.15,
            "callout_prob": 0.20,
            "ncns_prob": 0.02
        },
        "schedule_behavior": {
            "swap_request_prob": 0.25,
            "drop_request_prob": 0.30,
            "osm_offer_accept_prob": 0.20,
            "osm_offer_decline_prob": 0.80
        },
        "fairness_sensitivity": 0.75,
        "respect_sensitivity": 0.80,
        "safety_sensitivity": 0.90,
        "description": "Tries hard but easily drowns in pressure - often anxious and teary."
    },
    
    # =========================================================================
    # MID STAGE (30-180 days typical)
    # =========================================================================
    
    "workhorse": {
        "name": "Workhorse",
        "stage": "mid",
        "baseline": {
            "mood": 4.0,
            "felt_safe_prob": 0.92,
            "felt_fair_prob": 0.85,
            "felt_respected_prob": 0.88,
        },
        "volatility": {
            "mood": 0.25,
            "felt_safe_prob": 0.04,
            "felt_fair_prob": 0.06,
            "felt_respected_prob": 0.05,
        },
        "inertia": {
            "mood": 0.8,
            "felt_safe_prob": 0.8,
            "felt_fair_prob": 0.8,
            "felt_respected_prob": 0.8,
        },
        "attendance_bias": {
            "late_prob": 0.02,
            "callout_prob": 0.01,
            "ncns_prob": 0.0005
        },
        "schedule_behavior": {
            "swap_request_prob": 0.10,
            "drop_request_prob": 0.02,
            "osm_offer_accept_prob": 0.80,
            "osm_offer_decline_prob": 0.20
        },
        "fairness_sensitivity": 0.45,
        "respect_sensitivity": 0.50,
        "safety_sensitivity": 0.40,
        "description": "Reliable backbone of the team - always picks up extra shifts without complaint."
    },
    
    "ghoster_in_training": {
        "name": "Ghoster in Training",
        "stage": "mid",
        "baseline": {
            "mood": 2.8,
            "felt_safe_prob": 0.70,
            "felt_fair_prob": 0.50,
            "felt_respected_prob": 0.45,
        },
        "volatility": {
            "mood": 0.5,
            "felt_safe_prob": 0.10,
            "felt_fair_prob": 0.15,
            "felt_respected_prob": 0.15,
        },
        "inertia": {
            "mood": 0.5,
            "felt_safe_prob": 0.6,
            "felt_fair_prob": 0.6,
            "felt_respected_prob": 0.6,
        },
        "attendance_bias": {
            "late_prob": 0.35,
            "callout_prob": 0.25,
            "ncns_prob": 0.10
        },
        "schedule_behavior": {
            "swap_request_prob": 0.30,
            "drop_request_prob": 0.40,
            "osm_offer_accept_prob": 0.10,
            "osm_offer_decline_prob": 0.90
        },
        "fairness_sensitivity": 0.50,
        "respect_sensitivity": 0.60,
        "safety_sensitivity": 0.55,
        "description": "Frequently disappears without notice - has mastered the art of vanishing mid-shift."
    },
    
    "burned_idealist": {
        "name": "Burned Idealist",
        "stage": "mid",
        "baseline": {
            "mood": 2.2,
            "felt_safe_prob": 0.60,
            "felt_fair_prob": 0.35,
            "felt_respected_prob": 0.40,
        },
        "volatility": {
            "mood": 0.6,
            "felt_safe_prob": 0.12,
            "felt_fair_prob": 0.18,
            "felt_respected_prob": 0.15,
        },
        "inertia": {
            "mood": 0.7,
            "felt_safe_prob": 0.8,
            "felt_fair_prob": 0.85,
            "felt_respected_prob": 0.8,
        },
        "attendance_bias": {
            "late_prob": 0.25,
            "callout_prob": 0.22,
            "ncns_prob": 0.04
        },
        "schedule_behavior": {
            "swap_request_prob": 0.30,
            "drop_request_prob": 0.35,
            "osm_offer_accept_prob": 0.25,
            "osm_offer_decline_prob": 0.75
        },
        "fairness_sensitivity": 0.90,
        "respect_sensitivity": 0.85,
        "safety_sensitivity": 0.70,
        "description": "Once passionate, now bitter and resentful - bad days linger for weeks."
    },
    
    "social_glue": {
        "name": "Social Glue",
        "stage": "mid",
        "baseline": {
            "mood": 4.3,
            "felt_safe_prob": 0.94,
            "felt_fair_prob": 0.88,
            "felt_respected_prob": 0.92,
        },
        "volatility": {
            "mood": 0.35,
            "felt_safe_prob": 0.04,
            "felt_fair_prob": 0.06,
            "felt_respected_prob": 0.05,
        },
        "inertia": {
            "mood": 0.5,
            "felt_safe_prob": 0.5,
            "felt_fair_prob": 0.5,
            "felt_respected_prob": 0.5,
        },
        "attendance_bias": {
            "late_prob": 0.08,
            "callout_prob": 0.04,
            "ncns_prob": 0.002
        },
        "schedule_behavior": {
            "swap_request_prob": 0.18,
            "drop_request_prob": 0.08,
            "osm_offer_accept_prob": 0.75,
            "osm_offer_decline_prob": 0.25
        },
        "fairness_sensitivity": 0.70,
        "respect_sensitivity": 0.75,
        "safety_sensitivity": 0.60,
        "description": "Keeps morale high, organizes team events - always willing to help others with shifts."
    },
    
    "emerging_leader": {
        "name": "Emerging Leader",
        "stage": "mid",
        "baseline": {
            "mood": 4.2,
            "felt_safe_prob": 0.93,
            "felt_fair_prob": 0.87,
            "felt_respected_prob": 0.90,
        },
        "volatility": {
            "mood": 0.3,
            "felt_safe_prob": 0.04,
            "felt_fair_prob": 0.07,
            "felt_respected_prob": 0.05,
        },
        "inertia": {
            "mood": 0.6,
            "felt_safe_prob": 0.6,
            "felt_fair_prob": 0.6,
            "felt_respected_prob": 0.6,
        },
        "attendance_bias": {
            "late_prob": 0.03,
            "callout_prob": 0.02,
            "ncns_prob": 0.001
        },
        "schedule_behavior": {
            "swap_request_prob": 0.25,
            "drop_request_prob": 0.05,
            "osm_offer_accept_prob": 0.85,
            "osm_offer_decline_prob": 0.15
        },
        "fairness_sensitivity": 0.75,
        "respect_sensitivity": 0.80,
        "safety_sensitivity": 0.65,
        "description": "Shows strong leadership potential - volunteers for responsibility and motivates others."
    },
    
    # =========================================================================
    # LONG STAGE (180+ days typical)
    # =========================================================================
    
    "quiet_pro": {
        "name": "Quiet Pro",
        "stage": "long",
        "baseline": {
            "mood": 3.8,
            "felt_safe_prob": 0.95,
            "felt_fair_prob": 0.85,
            "felt_respected_prob": 0.88,
        },
        "volatility": {
            "mood": 0.2,
            "felt_safe_prob": 0.03,
            "felt_fair_prob": 0.05,
            "felt_respected_prob": 0.04,
        },
        "inertia": {
            "mood": 0.85,
            "felt_safe_prob": 0.85,
            "felt_fair_prob": 0.85,
            "felt_respected_prob": 0.85,
        },
        "attendance_bias": {
            "late_prob": 0.01,
            "callout_prob": 0.005,
            "ncns_prob": 0.0001
        },
        "schedule_behavior": {
            "swap_request_prob": 0.05,
            "drop_request_prob": 0.01,
            "osm_offer_accept_prob": 0.60,
            "osm_offer_decline_prob": 0.40
        },
        "fairness_sensitivity": 0.40,
        "respect_sensitivity": 0.45,
        "safety_sensitivity": 0.35,
        "description": "Speaks little, does everything right - the definition of steady reliability."
    },
    
    "cynical_anchor": {
        "name": "Cynical Anchor",
        "stage": "long",
        "baseline": {
            "mood": 2.5,
            "felt_safe_prob": 0.80,
            "felt_fair_prob": 0.45,
            "felt_respected_prob": 0.50,
        },
        "volatility": {
            "mood": 0.2,
            "felt_safe_prob": 0.05,
            "felt_fair_prob": 0.08,
            "felt_respected_prob": 0.06,
        },
        "inertia": {
            "mood": 0.92,
            "felt_safe_prob": 0.92,
            "felt_fair_prob": 0.92,
            "felt_respected_prob": 0.92,
        },
        "attendance_bias": {
            "late_prob": 0.05,
            "callout_prob": 0.03,
            "ncns_prob": 0.002
        },
        "schedule_behavior": {
            "swap_request_prob": 0.10,
            "drop_request_prob": 0.05,
            "osm_offer_accept_prob": 0.45,
            "osm_offer_decline_prob": 0.55
        },
        "fairness_sensitivity": 0.55,
        "respect_sensitivity": 0.50,
        "safety_sensitivity": 0.30,
        "description": "Grumpy veteran who's seen it all - still shows up every day because 'someone has to'."
    },
    
    "flight_risk_veteran": {
        "name": "Flight Risk Veteran",
        "stage": "long",
        "baseline": {
            "mood": 2.0,
            "felt_safe_prob": 0.55,
            "felt_fair_prob": 0.30,
            "felt_respected_prob": 0.35,
        },
        "volatility": {
            "mood": 0.5,
            "felt_safe_prob": 0.12,
            "felt_fair_prob": 0.18,
            "felt_respected_prob": 0.15,
        },
        "inertia": {
            "mood": 0.7,
            "felt_safe_prob": 0.8,
            "felt_fair_prob": 0.8,
            "felt_respected_prob": 0.8,
        },
        "attendance_bias": {
            "late_prob": 0.25,
            "callout_prob": 0.30,
            "ncns_prob": 0.08
        },
        "schedule_behavior": {
            "swap_request_prob": 0.50,
            "drop_request_prob": 0.35,
            "osm_offer_accept_prob": 0.15,
            "osm_offer_decline_prob": 0.85
        },
        "fairness_sensitivity": 0.80,
        "respect_sensitivity": 0.85,
        "safety_sensitivity": 0.75,
        "description": "Long-tenured but one bad week away from quitting forever."
    },
}


def get_persona_definition(key: str) -> Dict[str, Any]:
    """
    Return the persona definition for the given key.
    If the key is unknown, raise a KeyError with a clear message.
    """
    if key not in PERSONA_DEFINITIONS:
        raise KeyError(f"Unknown persona key: '{key}'. Available keys: {list_persona_keys()}")
    return PERSONA_DEFINITIONS[key]


def list_persona_keys() -> list[str]:
    """Return a list of all available persona keys."""
    return list(PERSONA_DEFINITIONS.keys())