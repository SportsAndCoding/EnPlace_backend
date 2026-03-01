"""
Shared personality scoring algorithm.
Used by both Stable Hire (candidate assessment) and Staff Personality (self-assessment).
"""

# Character weights for each dimension (0-100)
CHARACTER_SCORES = {
    "alex": {
        "autonomy": 75, "adaptability": 85, "conflict_tolerance": 80,
        "authority_response": 85, "team_orientation": 90, "feedback_reception": 80
    },
    "jordan": {
        "autonomy": 70, "adaptability": 75, "conflict_tolerance": 70,
        "authority_response": 70, "team_orientation": 75, "feedback_reception": 75
    },
    "taylor": {
        "autonomy": 85, "adaptability": 55, "conflict_tolerance": 50,
        "authority_response": 55, "team_orientation": 45, "feedback_reception": 50
    }
}

STABILITY_WEIGHTS = {
    "autonomy": 0.10, "adaptability": 0.20, "conflict_tolerance": 0.15,
    "authority_response": 0.15, "team_orientation": 0.25, "feedback_reception": 0.15
}

PERSONAS = {
    "steadyOperator": {
        "weights": {"adaptability": 0.3, "team_orientation": 0.3, "authority_response": 0.2, "feedback_reception": 0.2}
    },
    "quietContributor": {
        "weights": {"autonomy": 0.4, "team_orientation": 0.2, "conflict_tolerance": -0.2, "feedback_reception": -0.2}
    },
    "socialNavigator": {
        "weights": {"team_orientation": 0.4, "conflict_tolerance": 0.3, "adaptability": 0.2, "autonomy": -0.1}
    },
    "flightRisk": {
        "weights": {"adaptability": -0.3, "authority_response": -0.3, "team_orientation": -0.2, "feedback_reception": -0.2}
    }
}


def compute_fingerprint(scenario_rankings: dict) -> dict:
    """Compute 6-dimension fingerprint from scenario rankings."""
    fingerprint = {
        "autonomy": 0, "adaptability": 0, "conflict_tolerance": 0,
        "authority_response": 0, "team_orientation": 0, "feedback_reception": 0
    }
    count = 0
    for scenario, choice in scenario_rankings.items():
        choice_lower = choice.lower()
        if choice_lower in CHARACTER_SCORES:
            for dim, score in CHARACTER_SCORES[choice_lower].items():
                fingerprint[dim] += score
            count += 1
    if count > 0:
        for dim in fingerprint:
            fingerprint[dim] = round(fingerprint[dim] / count)
    return fingerprint


def compute_stability_score(fingerprint: dict) -> int:
    """Weighted average of fingerprint dimensions."""
    return round(sum(
        fingerprint[dim] * weight
        for dim, weight in STABILITY_WEIGHTS.items()
    ))


def compute_personas(fingerprint: dict) -> dict:
    """Compute persona probability distribution."""
    persona_scores = {}
    for persona_key, persona in PERSONAS.items():
        score = 50  # Base
        for dim, weight in persona["weights"].items():
            value = fingerprint.get(dim, 50)
            score += (value - 50) * weight
        persona_scores[persona_key] = max(0, min(100, score))
    
    # Normalize to percentages
    total = sum(persona_scores.values())
    if total > 0:
        persona_scores = {k: round((v / total) * 100) for k, v in persona_scores.items()}
    
    return persona_scores


def get_primary_persona(persona_scores: dict) -> str:
    """Return the key of the highest-scoring persona."""
    return max(persona_scores, key=persona_scores.get)


def compute_full_profile(scenario_rankings: dict) -> dict:
    """One-call convenience: rankings in, full profile out."""
    fingerprint = compute_fingerprint(scenario_rankings)
    stability_score = compute_stability_score(fingerprint)
    persona_scores = compute_personas(fingerprint)
    primary_persona = get_primary_persona(persona_scores)
    
    return {
        "fingerprint": fingerprint,
        "stability_score": stability_score,
        "persona_primary": primary_persona,
        "persona_scores": persona_scores
    }