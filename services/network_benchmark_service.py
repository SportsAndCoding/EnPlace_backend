"""
Network Benchmark Service
Computes real percentile rankings against synthetic + organic network.

UPDATED: Uses new schema (mood_emoji 1-5, felt_safe/felt_fair/felt_respected booleans)
"""

from typing import Dict, Any, List
from database.supabase_client import supabase


def compute_network_burnout_percentile(organic_burnout_score: float) -> Dict[str, Any]:
    """
    Compare an organic restaurant's burnout score against the synthetic network.
    
    Burnout score = weighted combination of:
    - Low mood (mood_emoji 1-5, lower = worse)
    - Low fairness rate (felt_fair = false)
    - Low respect rate (felt_respected = false)
    
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
        interpretation = f"Below average - worse than {100-percentile}% of network"
    else:
        interpretation = f"Needs attention - worse than {100-percentile}% of network"
    
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
    
    Burnout score formula (new schema):
    - (5 - avg_mood_emoji) * 0.4     -> low mood contributes
    - (1 - fair_rate) * 0.3          -> low fairness contributes  
    - (1 - respected_rate) * 0.3     -> low respect contributes
    
    Score range: 0 (no burnout) to ~4 (severe burnout)
    Normalized to 0-10 for comparison.
    """
    
    # Direct computation using new schema
    return compute_synthetic_burnout_direct()


def compute_synthetic_burnout_direct() -> List[float]:
    """
    Direct computation of burnout scores for all synthetic restaurants.
    Uses new schema: mood_emoji (1-5), felt_safe, felt_fair, felt_respected (booleans)
    """
    
    # Get max day_index to find "recent" data
    max_day_result = supabase.table("synthetic_daily_emotions") \
        .select("day_index") \
        .order("day_index", desc=True) \
        .limit(1) \
        .execute()
    
    if not max_day_result.data:
        return []
    
    max_day = max_day_result.data[0]["day_index"]
    recent_start = max_day - 7  # Last 7 days
    
    # Get aggregated emotions per restaurant for recent period
    # New schema: mood_emoji, felt_safe, felt_fair, felt_respected
    result = supabase.table("synthetic_daily_emotions") \
        .select("restaurant_id, mood_emoji, felt_safe, felt_fair, felt_respected") \
        .gte("day_index", recent_start) \
        .execute()
    
    if not result.data:
        return []
    
    # Aggregate by restaurant
    restaurant_data = {}
    for row in result.data:
        rid = row["restaurant_id"]
        if rid not in restaurant_data:
            restaurant_data[rid] = {
                "moods": [],
                "safe_count": 0,
                "fair_count": 0,
                "respected_count": 0,
                "total": 0
            }
        
        if row.get("mood_emoji") is not None:
            restaurant_data[rid]["moods"].append(row["mood_emoji"])
        
        restaurant_data[rid]["total"] += 1
        if row.get("felt_safe"):
            restaurant_data[rid]["safe_count"] += 1
        if row.get("felt_fair"):
            restaurant_data[rid]["fair_count"] += 1
        if row.get("felt_respected"):
            restaurant_data[rid]["respected_count"] += 1
    
    # Compute burnout score per restaurant
    scores = []
    for rid, data in restaurant_data.items():
        if data["moods"] and data["total"] > 0:
            avg_mood = sum(data["moods"]) / len(data["moods"])
            fair_rate = data["fair_count"] / data["total"]
            respected_rate = data["respected_count"] / data["total"]
            
            # Burnout formula: higher = worse
            # Low mood: (5 - mood) gives 0-4, multiply by 0.4
            # Low fairness: (1 - rate) gives 0-1, multiply by 0.3
            # Low respect: (1 - rate) gives 0-1, multiply by 0.3
            raw_burnout = (
                (5 - avg_mood) * 0.4 +
                (1 - fair_rate) * 3 +  # Scale 0-1 to 0-3
                (1 - respected_rate) * 3  # Scale 0-1 to 0-3
            )
            
            # Normalize to 0-10 scale
            # Max raw = (5-1)*0.4 + 3 + 3 = 1.6 + 6 = 7.6
            burnout = min(10, raw_burnout * 1.3)
            
            scores.append(burnout)
    
    return scores


def compute_organic_burnout_score(checkins_7d: list) -> float:
    """
    Compute burnout score for an organic restaurant.
    
    Uses same formula as synthetic for apples-to-apples comparison.
    Schema: mood_emoji (1-5), felt_safe, felt_fair, felt_respected (booleans)
    """
    
    if not checkins_7d:
        return 5.0  # Neutral score if no data
    
    # Calculate averages
    moods = [c.get("mood_emoji", 3) for c in checkins_7d if c.get("mood_emoji") is not None]
    avg_mood = sum(moods) / len(moods) if moods else 3
    
    total = len(checkins_7d)
    fair_count = sum(1 for c in checkins_7d if c.get("felt_fair"))
    respected_count = sum(1 for c in checkins_7d if c.get("felt_respected"))
    
    fair_rate = fair_count / total if total > 0 else 0.5
    respected_rate = respected_count / total if total > 0 else 0.5
    
    # Same burnout formula as synthetic
    raw_burnout = (
        (5 - avg_mood) * 0.4 +
        (1 - fair_rate) * 3 +
        (1 - respected_rate) * 3
    )
    
    # Normalize to 0-10 scale
    burnout = min(10, raw_burnout * 1.3)
    
    return burnout