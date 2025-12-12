"""
Network Benchmark Service
Computes real percentile rankings against synthetic + organic network.

UPDATED: 
- Uses new schema (mood_emoji 1-5, felt_safe/felt_fair/felt_respected booleans)
- Includes SMA (Staff-Manager Alignment) network comparison
"""

from typing import Dict, Any, List
from database.supabase_client import supabase


# ═══════════════════════════════════════════════════════════════════════════════
# BURNOUT BENCHMARKING
# ═══════════════════════════════════════════════════════════════════════════════

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
    """
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
            
            raw_burnout = (
                (5 - avg_mood) * 0.4 +
                (1 - fair_rate) * 3 +
                (1 - respected_rate) * 3
            )
            
            burnout = min(10, raw_burnout * 1.3)
            scores.append(burnout)
    
    return scores


def compute_organic_burnout_score(checkins_7d: list) -> float:
    """
    Compute burnout score for an organic restaurant.
    """
    
    if not checkins_7d:
        return 5.0
    
    moods = [c.get("mood_emoji", 3) for c in checkins_7d if c.get("mood_emoji") is not None]
    avg_mood = sum(moods) / len(moods) if moods else 3
    
    total = len(checkins_7d)
    fair_count = sum(1 for c in checkins_7d if c.get("felt_fair"))
    respected_count = sum(1 for c in checkins_7d if c.get("felt_respected"))
    
    fair_rate = fair_count / total if total > 0 else 0.5
    respected_rate = respected_count / total if total > 0 else 0.5
    
    raw_burnout = (
        (5 - avg_mood) * 0.4 +
        (1 - fair_rate) * 3 +
        (1 - respected_rate) * 3
    )
    
    burnout = min(10, raw_burnout * 1.3)
    
    return burnout


# ═══════════════════════════════════════════════════════════════════════════════
# SMA (STAFF-MANAGER ALIGNMENT) BENCHMARKING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_network_sma_percentile(organic_sma_score: float) -> Dict[str, Any]:
    """
    Compare an organic restaurant's SMA score against the synthetic network.
    
    SMA = how closely manager perception matches staff reality.
    Higher = better alignment.
    
    Returns percentile rank and interpretation.
    """
    
    network_scores = get_synthetic_sma_scores()
    
    if not network_scores:
        return {
            "percentile": 50,
            "interpretation": "Network data unavailable",
            "network_size": 0
        }
    
    # Count how many restaurants have WORSE (lower) SMA than organic
    # Higher SMA is better, so we count how many are lower
    worse_count = sum(1 for score in network_scores if score < organic_sma_score)
    
    percentile = int((worse_count / len(network_scores)) * 100)
    
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
        "organic_score": round(organic_sma_score, 2),
        "network_avg": round(sum(network_scores) / len(network_scores), 2)
    }


def get_synthetic_sma_scores() -> List[float]:
    """
    Compute SMA score for each synthetic restaurant.
    
    SMA = alignment between manager's overall_rating and staff's avg mood.
    """
    
    # Get max day_index for recent data
    max_day_result = supabase.table("synthetic_daily_emotions") \
        .select("day_index") \
        .order("day_index", desc=True) \
        .limit(1) \
        .execute()
    
    if not max_day_result.data:
        return []
    
    max_day = max_day_result.data[0]["day_index"]
    recent_start = max_day - 7
    
    # Get staff emotions by restaurant and day
    emotions_result = supabase.table("synthetic_daily_emotions") \
        .select("restaurant_id, day_index, mood_emoji") \
        .gte("day_index", recent_start) \
        .execute()
    
    if not emotions_result.data:
        return []
    
    # Get manager logs for same period
    manager_result = supabase.table("synthetic_manager_logs") \
        .select("restaurant_id, day_index, overall_rating") \
        .gte("day_index", recent_start) \
        .execute()
    
    if not manager_result.data:
        return []
    
    # Aggregate staff mood by restaurant+day
    staff_by_day = {}
    for row in emotions_result.data:
        rid = row["restaurant_id"]
        day = row["day_index"]
        key = (rid, day)
        
        if key not in staff_by_day:
            staff_by_day[key] = []
        
        if row.get("mood_emoji") is not None:
            staff_by_day[key].append(row["mood_emoji"])
    
    # Index manager ratings by restaurant+day
    manager_by_day = {}
    for row in manager_result.data:
        rid = row["restaurant_id"]
        day = row["day_index"]
        key = (rid, day)
        manager_by_day[key] = row.get("overall_rating")
    
    # Calculate SMA per restaurant
    restaurant_alignments = {}
    
    for (rid, day), moods in staff_by_day.items():
        if not moods:
            continue
        
        staff_avg = sum(moods) / len(moods)
        manager_rating = manager_by_day.get((rid, day))
        
        if manager_rating is None:
            continue
        
        if rid not in restaurant_alignments:
            restaurant_alignments[rid] = {"aligned": 0, "total": 0}
        
        restaurant_alignments[rid]["total"] += 1
        
        # Aligned if within 1 point
        if abs(staff_avg - manager_rating) <= 1.0:
            restaurant_alignments[rid]["aligned"] += 1
    
    # Compute SMA score (0-100) for each restaurant
    scores = []
    for rid, data in restaurant_alignments.items():
        if data["total"] > 0:
            alignment_rate = data["aligned"] / data["total"]
            sma_score = alignment_rate * 100
            scores.append(sma_score)
    
    return scores


def compute_organic_sma_score(checkins_7d: list, manager_logs_7d: list) -> float:
    """
    Compute SMA score for an organic restaurant.
    
    Compares manager daily ratings to staff daily avg mood.
    Returns 0-100 score.
    """
    
    if not checkins_7d or not manager_logs_7d:
        return 50.0  # Neutral if no data
    
    # Group staff moods by date
    staff_by_date = {}
    for checkin in checkins_7d:
        date = checkin.get("checkin_date")
        if date and checkin.get("mood_emoji") is not None:
            if date not in staff_by_date:
                staff_by_date[date] = []
            staff_by_date[date].append(checkin["mood_emoji"])
    
    # Compare each manager log to staff avg
    aligned = 0
    total = 0
    
    for log in manager_logs_7d:
        log_date = log.get("log_date")
        manager_rating = log.get("overall_rating")
        
        if not log_date or manager_rating is None:
            continue
        
        staff_moods = staff_by_date.get(log_date, [])
        if not staff_moods:
            continue
        
        staff_avg = sum(staff_moods) / len(staff_moods)
        total += 1
        
        # Aligned if within 1 point
        if abs(staff_avg - manager_rating) <= 1.0:
            aligned += 1
    
    if total == 0:
        return 50.0
    
    return (aligned / total) * 100


# ═══════════════════════════════════════════════════════════════════════════════
# FAIRNESS BENCHMARKING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_network_fairness_percentile(organic_fairness_score: float) -> Dict[str, Any]:
    """
    Compare an organic restaurant's fairness score against the synthetic network.
    
    Fairness = felt_fair rate from check-ins.
    Higher = better.
    """
    
    network_scores = get_synthetic_fairness_scores()
    
    if not network_scores:
        return {
            "percentile": 50,
            "interpretation": "Network data unavailable",
            "network_size": 0
        }
    
    # Higher is better, count how many are lower
    worse_count = sum(1 for score in network_scores if score < organic_fairness_score)
    
    percentile = int((worse_count / len(network_scores)) * 100)
    
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
        "organic_score": round(organic_fairness_score, 2),
        "network_avg": round(sum(network_scores) / len(network_scores), 2)
    }


def get_synthetic_fairness_scores() -> List[float]:
    """
    Compute fairness score (felt_fair rate * 100) for each synthetic restaurant.
    """
    
    max_day_result = supabase.table("synthetic_daily_emotions") \
        .select("day_index") \
        .order("day_index", desc=True) \
        .limit(1) \
        .execute()
    
    if not max_day_result.data:
        return []
    
    max_day = max_day_result.data[0]["day_index"]
    recent_start = max_day - 7
    
    result = supabase.table("synthetic_daily_emotions") \
        .select("restaurant_id, felt_fair") \
        .gte("day_index", recent_start) \
        .execute()
    
    if not result.data:
        return []
    
    # Aggregate by restaurant
    restaurant_data = {}
    for row in result.data:
        rid = row["restaurant_id"]
        if rid not in restaurant_data:
            restaurant_data[rid] = {"fair_count": 0, "total": 0}
        
        restaurant_data[rid]["total"] += 1
        if row.get("felt_fair"):
            restaurant_data[rid]["fair_count"] += 1
    
    # Compute fairness score per restaurant
    scores = []
    for rid, data in restaurant_data.items():
        if data["total"] > 0:
            fairness = (data["fair_count"] / data["total"]) * 100
            scores.append(fairness)
    
    return scores


def compute_organic_fairness_score(checkins_7d: list) -> float:
    """
    Compute fairness score for an organic restaurant.
    Returns 0-100.
    """
    
    if not checkins_7d:
        return 50.0
    
    total = len(checkins_7d)
    fair_count = sum(1 for c in checkins_7d if c.get("felt_fair"))
    
    return (fair_count / total) * 100 if total > 0 else 50.0