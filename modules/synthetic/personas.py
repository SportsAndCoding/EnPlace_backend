from typing import Dict, Any

PERSONA_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "enthusiastic_rookie": {
        "name": "Enthusiastic Rookie",
        "stage": "rookie",
        "baseline": {
            "fairness": 7.0,
            "mood": 8.5,
            "stress": 3.0,
            "energy": 8.5,
            "workload_satisfaction": 8.0
        },
        "volatility": {
            "mood": 1.2,
            "stress": 1.0,
            "energy": 1.1,
            "workload_satisfaction": 1.0,
            "fairness": 1.0,
        },
        "inertia": {
            "mood": 0.4,
            "stress": 0.5,
            "energy": 0.4,
            "workload_satisfaction": 0.5,
            "fairness": 0.5,
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
        "stress_sensitivity": 0.55,
        "description": "Eager new hire who loves the job and wants to prove themselves."
    },
    "lazy_rookie": {
        "name": "Lazy Rookie",
        "stage": "rookie",
        "baseline": {
            "fairness": 5.5,
            "mood": 6.0,
            "stress": 3.2,
            "energy": 4.0,
            "workload_satisfaction": 5.5
        },
        "volatility": {
            "mood": 0.8,
            "stress": 0.7,
            "energy": 0.9,
            "workload_satisfaction": 0.8,
            "fairness": 0.8,
        },
        "inertia": {
            "mood": 0.5,
            "stress": 0.5,
            "energy": 0.6,
            "workload_satisfaction": 0.5,
            "fairness": 0.5,
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
        "stress_sensitivity": 0.40,
        "description": "Does the bare minimum and shows up only when it suits them."
    },
    "snarky_rookie": {
        "name": "Snarky Rookie",
        "stage": "rookie",
        "baseline": {
            "fairness": 4.5,
            "mood": 5.5,
            "stress": 4.5,
            "energy": 6.0,
            "workload_satisfaction": 6.0
        },
        "volatility": {
            "mood": 1.5,
            "stress": 1.2,
            "energy": 1.0,
            "workload_satisfaction": 1.1,
            "fairness": 1.1,
        },
        "inertia": {
            "mood": 0.4,
            "stress": 0.5,
            "energy": 0.5,
            "workload_satisfaction": 0.5,
            "fairness": 0.5,
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
        "stress_sensitivity": 0.65,
        "description": "Quick-witted new hire with a sharp tongue — complains but still gets the job done."
    },
    "overwhelmed_rookie": {
        "name": "Overwhelmed Rookie",
        "stage": "rookie",
        "baseline": {
            "fairness": 4.0,
            "mood": 5.0,
            "stress": 7.0,
            "energy": 4.5,
            "workload_satisfaction": 4.0
        },
        "volatility": {
            "mood": 1.4,
            "stress": 1.6,
            "energy": 1.3,
            "workload_satisfaction": 1.5,
            "fairness": 1.5,
        },
        "inertia": {
            "mood": 0.6,
            "stress": 0.7,
            "energy": 0.6,
            "workload_satisfaction": 0.7,
            "fairness": 0.7,
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
        "stress_sensitivity": 0.90,
        "description": "Tries hard but easily drowns in pressure — often anxious and teary."
    },
    "workhorse": {
        "name": "Workhorse",
        "stage": "mid",
        "baseline": {
            "fairness": 6.8,
            "mood": 7.0,
            "stress": 5.5,
            "energy": 7.5,
            "workload_satisfaction": 7.5
        },
        "volatility": {
            "mood": 0.6,
            "stress": 0.8,
            "energy": 0.7,
            "workload_satisfaction": 0.6,
            "fairness": 0.6,
        },
        "inertia": {
            "mood": 0.8,
            "stress": 0.8,
            "energy": 0.8,
            "workload_satisfaction": 0.8,
            "fairness": 0.8,
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
        "stress_sensitivity": 0.40,
        "description": "Reliable backbone of the team — always picks up extra shifts without complaint."
    },
    "ghoster_in_training": {
        "name": "Ghoster in Training",
        "stage": "mid",
        "baseline": {
            "fairness": 4.8,
            "mood": 5.5,
            "stress": 6.0,
            "energy": 5.0,
            "workload_satisfaction": 4.5
        },
        "volatility": {
            "mood": 1.3,
            "stress": 1.4,
            "energy": 1.2,
            "workload_satisfaction": 1.3,
            "fairness": 1.3,
        },
        "inertia": {
            "mood": 0.5,
            "stress": 0.6,
            "energy": 0.5,
            "workload_satisfaction": 0.6,
            "fairness": 0.6,
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
        "stress_sensitivity": 0.60,
        "description": "Frequently disappears without notice — has mastered the art of vanishing mid-shift."
    },
    "burned_idealist": {
        "name": "Burned Idealist",
        "stage": "mid",
        "baseline": {
            "fairness": 3.8,
            "mood": 5.0,
            "stress": 7.5,
            "energy": 4.5,
            "workload_satisfaction": 3.5
        },
        "volatility": {
            "mood": 1.8,
            "stress": 1.7,
            "energy": 1.4,
            "workload_satisfaction": 1.6,
            "fairness": 1.6,
        },
        "inertia": {
            "mood": 0.7,
            "stress": 0.85,
            "energy": 0.7,
            "workload_satisfaction": 0.8,
            "fairness": 0.8,
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
        "stress_sensitivity": 0.80,
        "description": "Once passionate, now bitter and resentful — bad days linger for weeks."
    },
    "social_glue": {
        "name": "Social Glue",
        "stage": "mid",
        "baseline": {
            "fairness": 7.2,
            "mood": 8.0,
            "stress": 4.5,
            "energy": 7.5,
            "workload_satisfaction": 7.8
        },
        "volatility": {
            "mood": 1.0,
            "stress": 0.9,
            "energy": 0.8,
            "workload_satisfaction": 0.7,
            "fairness": 0.7,
        },
        "inertia": {
            "mood": 0.5,
            "stress": 0.5,
            "energy": 0.5,
            "workload_satisfaction": 0.5,
            "fairness": 0.5,
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
        "stress_sensitivity": 0.45,
        "description": "Keeps morale high, organizes team events — always willing to help others with shifts."
    },
    "quiet_pro": {
        "name": "Quiet Pro",
        "stage": "long",
        "baseline": {
            "fairness": 6.8,
            "mood": 7.2,
            "stress": 4.0,
            "energy": 7.0,
            "workload_satisfaction": 8.0
        },
        "volatility": {
            "mood": 0.5,
            "stress": 0.6,
            "energy": 0.5,
            "workload_satisfaction": 0.5,
            "fairness": 0.5,
        },
        "inertia": {
            "mood": 0.85,
            "stress": 0.8,
            "energy": 0.85,
            "workload_satisfaction": 0.85,
            "fairness": 0.85,
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
        "stress_sensitivity": 0.30,
        "description": "Speaks little, does everything right — the definition of steady reliability."
    },
    "cynical_anchor": {
        "name": "Cynical Anchor",
        "stage": "long",
        "baseline": {
            "fairness": 4.2,
            "mood": 4.5,
            "stress": 6.5,
            "energy": 5.0,
            "workload_satisfaction": 5.5
        },
        "volatility": {
            "mood": 0.5,
            "stress": 0.5,
            "energy": 0.5,
            "workload_satisfaction": 0.5,
            "fairness": 0.5,
        },
        "inertia": {
            "mood": 0.92,
            "stress": 0.92,
            "energy": 0.92,
            "workload_satisfaction": 0.92,
            "fairness": 0.92,
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
        "stress_sensitivity": 0.20,
        "description": "Grumpy veteran who’s seen it all — still shows up every day because “someone has to”."
    },
    "flight_risk_veteran": {
        "name": "Flight Risk Veteran",
        "stage": "long",
        "baseline": {
            "fairness": 3.5,
            "mood": 4.5,
            "stress": 7.2,
            "energy": 4.2,
            "workload_satisfaction": 3.0
        },
        "volatility": {
            "mood": 1.6,
            "stress": 1.5,
            "energy": 1.4,
            "workload_satisfaction": 1.7,
            "fairness": 1.7,
        },
        "inertia": {
            "mood": 0.7,
            "stress": 0.8,
            "energy": 0.7,
            "workload_satisfaction": 0.8,
            "fairness": 0.8,
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
        "stress_sensitivity": 0.75,
        "description": "Long-tenured but one bad week away from quitting forever."
    },
    "emerging_leader": {
        "name": "Emerging Leader",
        "stage": "mid",
        "baseline": {
            "fairness": 7.0,
            "mood": 7.8,
            "stress": 5.0,
            "energy": 8.0,
            "workload_satisfaction": 7.5
        },
        "volatility": {
            "mood": 0.9,
            "stress": 1.0,
            "energy": 0.8,
            "workload_satisfaction": 0.9,
            "fairness": 0.9,
        },
        "inertia": {
            "mood": 0.6,
            "stress": 0.6,
            "energy": 0.6,
            "workload_satisfaction": 0.6,
            "fairness": 0.6,
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
        "stress_sensitivity": 0.60,
        "description": "Shows strong leadership potential — volunteers for responsibility and motivates others."
    }
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
