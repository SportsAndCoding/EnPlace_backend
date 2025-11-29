"""
modules/synthetic/restaurant_profiles.py

Static definitions for restaurant operational archetypes.
These profiles provide deterministic environmental modifiers that influence
staff emotional trends, fairness perception, stress load, and behavioral
probabilities throughout the simulation.
"""

from typing import Dict, Any, List

RESTAURANT_PROFILES: Dict[str, Dict[str, Any]] = {
    "steakhouse": {
        "name": "Classic Steakhouse",
        "type": "steakhouse",
        "volume_intensity": 0.75,
        "guest_difficulty": 0.90,
        "tip_variance": 0.85,
        "manager_fairness": 0.80,
        "crew_cohesion": 0.85,
        "burnout_multiplier": 1.10,
        "swap_culture": 0.60,
        "shift_length_avg": 8.5,
        "rush_curve": [0.2, 0.4, 0.7, 0.9, 0.6],
        "description": "High-check, high-expectation dining. Demanding guests, big tips, strong veteran crew.",
    },
    "upscale_casual": {
        "name": "Upscale Casual",
        "type": "upscale_casual",
        "volume_intensity": 0.70,
        "guest_difficulty": 0.75,
        "tip_variance": 0.65,
        "manager_fairness": 0.85,
        "crew_cohesion": 0.80,
        "burnout_multiplier": 0.95,
        "swap_culture": 0.75,
        "shift_length_avg": 8.0,
        "rush_curve": [0.3, 0.5, 0.8, 0.9, 0.7],
        "description": "Trendy, chef-driven spots with craft cocktails. Good money, supportive management, social staff.",
    },
    "family_diner": {
        "name": "Family Diner",
        "type": "family_diner",
        "volume_intensity": 0.60,
        "guest_difficulty": 0.35,
        "tip_variance": 0.30,
        "manager_fairness": 0.70,
        "crew_cohesion": 0.90,
        "burnout_multiplier": 0.70,
        "swap_culture": 0.50,
        "shift_length_avg": 7.5,
        "rush_curve": [0.8, 0.6, 0.4, 0.3, 0.3],
        "description": "Breakfast and lunch heavy, older regular guests, very stable and familial atmosphere.",
    },
    "breakfast_cafe": {
        "name": "Breakfast Café",
        "type": "breakfast_cafe",
        "volume_intensity": 0.55,
        "guest_difficulty": 0.30,
        "tip_variance": 0.25,
        "manager_fairness": 0.85,
        "crew_cohesion": 0.80,
        "burnout_multiplier": 0.60,
        "swap_culture": 0.65,
        "shift_length_avg": 6.5,
        "rush_curve": [0.9, 0.7, 0.4, 0.2, 0.1],
        "description": "Morning rush only, relaxed pace afterward, friendly regulars, early closings.",
    },
    "bar_and_grille": {
        "name": "Bar & Grille",
        "type": "bar_grille",
        "volume_intensity": 0.80,
        "guest_difficulty": 0.70,
        "tip_variance": 0.80,
        "manager_fairness": 0.65,
        "crew_cohesion": 0.70,
        "burnout_multiplier": 1.25,
        "swap_culture": 0.80,
        "shift_length_avg": 9.0,
        "rush_curve": [0.2, 0.3, 0.6, 0.9, 1.0],
        "description": "Late nights, alcohol-driven chaos, high earnings potential but unpredictable.",
    },
    "sports_bar": {
        "name": "Sports Bar",
        "type": "sports_bar",
        "volume_intensity": 0.90,
        "guest_difficulty": 0.80,
        "tip_variance": 0.90,
        "manager_fairness": 0.55,
        "crew_cohesion": 0.50,
        "burnout_multiplier": 1.40,
        "swap_culture": 0.85,
        "shift_length_avg": 9.5,
        "rush_curve": [0.1, 0.2, 0.5, 0.8, 1.0],
        "description": "Game days are insane, slow days are dead. High volatility defines the culture.",
    },
    "fast_casual": {
        "name": "Fast Casual Chain",
        "type": "fast_casual",
        "volume_intensity": 0.85,
        "guest_difficulty": 0.60,
        "tip_variance": 0.20,
        "manager_fairness": 0.60,
        "crew_cohesion": 0.55,
        "burnout_multiplier": 1.35,
        "swap_culture": 0.40,
        "shift_length_avg": 7.0,
        "rush_curve": [0.6, 0.8, 0.9, 0.8, 0.7],
        "description": "Corporate systems, tight labor budgets, repetitive work, high turnover.",
    },
    "hotel_restaurant": {
        "name": "Hotel Restaurant",
        "type": "hotel_restaurant",
        "volume_intensity": 0.65,
        "guest_difficulty": 0.95,
        "tip_variance": 0.70,
        "manager_fairness": 0.70,
        "crew_cohesion": 0.75,
        "burnout_multiplier": 1.15,
        "swap_culture": 0.55,
        "shift_length_avg": 9.0,
        "rush_curve": [0.7, 0.8, 0.6, 0.5, 0.5],
        "description": "Entitled travelers, irregular rushes, corporate oversight, long breaks between peaks.",
    },
    "airport_restaurant": {
        "name": "Airport Location",
        "type": "airport_restaurant",
        "volume_intensity": 0.95,
        "guest_difficulty": 0.92,
        "tip_variance": 0.60,
        "manager_fairness": 0.50,
        "crew_cohesion": 0.45,
        "burnout_multiplier": 1.50,
        "swap_culture": 0.30,
        "shift_length_avg": 8.5,
        "rush_curve": [0.8, 0.9, 0.9, 0.8, 0.7],
        "description": "Constant rush, stressed travelers, security restrictions, extremely high burnout.",
    },
    "college_town_cafe": {
        "name": "College Town Café",
        "type": "college_cafe",
        "volume_intensity": 0.70,
        "guest_difficulty": 0.50,
        "tip_variance": 0.55,
        "manager_fairness": 0.75,
        "crew_cohesion": 0.70,
        "burnout_multiplier": 0.90,
        "swap_culture": 0.90,
        "shift_length_avg": 6.0,
        "rush_curve": [0.5, 0.8, 0.7, 0.6, 0.4],
        "description": "Young staff, flexible scheduling, creative vibe, tips vary with student cash flow.",
    },
    "high_volume_chain": {
        "name": "High-Volume National Chain",
        "type": "high_volume_chain",
        "volume_intensity": 0.92,
        "guest_difficulty": 0.70,
        "tip_variance": 0.40,
        "manager_fairness": 0.55,
        "crew_cohesion": 0.50,
        "burnout_multiplier": 1.45,
        "swap_culture": 0.45,
        "shift_length_avg": 8.0,
        "rush_curve": [0.7, 0.9, 1.0, 0.9, 0.8],
        "description": "Corporate metrics, relentless pace, scripted service, high staff churn.",
    },
    "neighborhood_bistro": {
        "name": "Neighborhood Bistro",
        "type": "neighborhood_bistro",
        "volume_intensity": 0.60,
        "guest_difficulty": 0.45,
        "tip_variance": 0.50,
        "manager_fairness": 0.90,
        "crew_cohesion": 0.95,
        "burnout_multiplier": 0.75,
        "swap_culture": 0.70,
        "shift_length_avg": 7.5,
        "rush_curve": [0.3, 0.5, 0.8, 0.9, 0.7],
        "description": "Loyal locals, owner-operated warmth, low drama, long tenures.",
    },
}


def get_profile(profile_key: str) -> Dict[str, Any]:
    """
    Return the restaurant profile dictionary for the given key.
    Raises KeyError with helpful message if unknown.
    """
    if profile_key not in RESTAURANT_PROFILES:
        raise KeyError(
            f"Unknown restaurant profile '{profile_key}'. "
            f"Available profiles: {list_profile_keys()}"
        )
    return RESTAURANT_PROFILES[profile_key]


def list_profile_keys() -> List[str]:
    """Return a list of all available restaurant profile keys."""
    return list(RESTAURANT_PROFILES.keys())