"""
Network Benchmark Service
Computes real percentile rankings against synthetic + organic network.
"""

from typing import Dict, Any, List
from database.supabase_client import supabase


def compute_network_burnout_percentile(organic_burnout_score: float) -> Dict[str, Any]:
    """
    Compare an organic restaurant's burnout score against the synthetic network.
    
    Burnout score = weighted combination of:
    - Low mood (below 5 on 1-10 scale)
    - High stress (above 6 on 1-10 scale)
    - Low energy (below 5 on 1-10 scale)
    
    Lower burnout score = healthier restaurant
    
    Returns percentile rank and interpretation.
    """
    
    # Get synthetic network burnout scores
    network_scores = get_synthetic_burnout_scores()
    
    if not network_scores:
        return {
            "percentile": 50,
            "interpretation": "Network data unavailable",
            "network_size": 0
        }
    
    # Count how many restaurants have WORSE (higher) burnout than organic
    worse_count = sum(1 for score in network_scores if score > organic_burnout_score)
    
    # Percentile = % of network you're better than
    percentile = int((worse_count / len(network_scores)) * 100)
    
    # Interpretation
    if percentile >= 75:
        interpretation = f"Better than {percentile}% of network"
    elif percentile >= 50:
        interpretation = f"Better than {percentile}% of network"
    elif percentile >= 25:
        interpretation = f"Below average — worse than {100-percentile}% of network"
    else:
        interpretation = f"Needs attention — worse than {100-percentile}% of network"
    
    return {
        "percentile": percentile,
        "interpretation": interpretation,
        "network_size": len(network_scores),
        "organic_score": round(organic_burnout_score, 2),
        "network_avg": round(sum(network_scores) / len(network_scores), 2)
    }


def get_synthetic_burnout_scores() -> List[float]:
    """
    Compute burnout score for each synthetic restaurant.
    
    Burnout score formula:
    - (10 - avg_mood) * 0.4      → low mood contributes
    - (avg_stress - 5) * 0.3     → high stress contributes  
    - (10 - avg_energy) * 0.3    → low energy contributes
    
    Score range: 0 (no burnout) to 10 (severe burnout)
    """
    
    # Aggregate emotions by restaurant (last 7 days equivalent = last 7 day_index values)
    # Since synthetic data uses day_index, we'll use the most recent data per restaurant
    result = supabase.rpc('get_synthetic_burnout_scores').execute()
    
    if result.data:
        return [r['burnout_score'] for r in result.data if r['burnout_score'] is not None]
    
    # Fallback: compute directly if RPC doesn't exist
    return compute_synthetic_burnout_direct()


def compute_synthetic_burnout_direct() -> List[float]:
    """
    Direct computation of burnout scores for all synthetic restaurants.
    Used as fallback if RPC not available.
    """
    
    # Get max day_index to find "recent" data
    max_day_result = supabase.table("synthetic_daily_emotions").select("day_index").order("day_index", desc=True).limit(1).execute()
    
    if not max_day_result.data:
        return []
    
    max_day = max_day_result.data[0]["day_index"]
    recent_start = max_day - 7  # Last 7 days
    
    # Get aggregated emotions per restaurant for recent period
    result = supabase.table("synthetic_daily_emotions").select(
        "restaurant_id, mood, stress, energy"
    ).gte("day_index", recent_start).execute()
    
    if not result.data:
        return []
    
    # Aggregate by restaurant
    restaurant_data = {}
    for row in result.data:
        rid = row["restaurant_id"]
        if rid not in restaurant_data:
            restaurant_data[rid] = {"moods": [], "stresses": [], "energies": []}
        
        if row.get("mood") is not None:
            restaurant_data[rid]["moods"].append(row["mood"])
        if row.get("stress") is not None:
            restaurant_data[rid]["stresses"].append(row["stress"])
        if row.get("energy") is not None:
            restaurant_data[rid]["energies"].append(row["energy"])
    
    # Compute burnout score per restaurant
    scores = []
    for rid, data in restaurant_data.items():
        if data["moods"] and data["stresses"] and data["energies"]:
            avg_mood = sum(data["moods"]) / len(data["moods"])
            avg_stress = sum(data["stresses"]) / len(data["stresses"])
            avg_energy = sum(data["energies"]) / len(data["energies"])
            
            # Burnout formula: higher = worse
            # Low mood (10 - mood), high stress (stress - 5), low energy (10 - energy)
            burnout = (
                (10 - avg_mood) * 0.4 +
                max(0, avg_stress - 5) * 0.3 +
                (10 - avg_energy) * 0.3
            )
            
            scores.append(burnout)
    
    return scores


def compute_organic_burnout_score(checkins_7d: list) -> float:
    """
    Compute burnout score for an organic restaurant.
    
    Converts 1-5 mood scale to 1-10 for comparison with synthetic.
    Uses felt_fair and felt_respected as additional signals.
    """
    
    if not checkins_7d:
        return 5.0  # Neutral score if no data
    
    # Convert mood_emoji (1-5) to 1-10 scale
    moods_converted = [(c.get("mood_emoji", 3) * 2) for c in checkins_7d]
    avg_mood = sum(moods_converted) / len(moods_converted)
    
    # Use felt_fair and felt_respected as fairness proxy (true = 8, false = 3)
    fairness_scores = []
    for c in checkins_7d:
        fair = c.get("felt_fair")
        respected = c.get("felt_respected")
        if fair is not None and respected is not None:
            score = 8 if (fair and respected) else 5 if (fair or respected) else 3
            fairness_scores.append(score)
    
    avg_fairness = sum(fairness_scores) / len(fairness_scores) if fairness_scores else 6
    
    # Estimate stress/energy from mood (correlation assumption)
    # Lower mood → higher stress, lower energy
    estimated_stress = 10 - avg_mood + 2  # Inverse of mood
    estimated_energy = avg_mood - 1  # Correlated with mood
    
    # Clamp to 1-10
    estimated_stress = max(1, min(10, estimated_stress))
    estimated_energy = max(1, min(10, estimated_energy))
    
    # Burnout formula (same as synthetic)
    burnout = (
        (10 - avg_mood) * 0.4 +
        max(0, estimated_stress - 5) * 0.3 +
        (10 - estimated_energy) * 0.3
    )
    
    return burnout