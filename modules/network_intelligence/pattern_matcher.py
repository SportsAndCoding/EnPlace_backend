"""
modules/network_intelligence/pattern_matcher.py

The brain of En Place's predictive engine.

1. Extracts "quitter signatures" from synthetic network data
2. Compares organic staff check-in trajectories to those signatures
3. Outputs flight risk scores (0-100)

Data schema (both synthetic and organic):
- mood_emoji: integer 1-5
- felt_safe: boolean
- felt_fair: boolean
- felt_respected: boolean
"""

from dataclasses import dataclass
from typing import Dict, List, Any, Optional
import statistics


# =============================================================================
# TENURE BUCKETS - aligned with turnover research
# =============================================================================

TENURE_BUCKETS = [
    {"name": "first_week", "min": 0, "max": 7, "label": "First Week"},
    {"name": "week_2", "min": 8, "max": 14, "label": "Week 2"},
    {"name": "month_1", "min": 15, "max": 30, "label": "Month 1"},
    {"name": "month_2", "min": 31, "max": 60, "label": "Month 2"},
    {"name": "90_day_cliff", "min": 61, "max": 90, "label": "90-Day Cliff"},
    {"name": "post_cliff", "min": 91, "max": 180, "label": "Post-Cliff"},
    {"name": "veteran", "min": 181, "max": 9999, "label": "Veteran"},
]


def get_tenure_bucket(tenure_days: int) -> Dict[str, Any]:
    """Return the bucket config for a given tenure."""
    for bucket in TENURE_BUCKETS:
        if bucket["min"] <= tenure_days <= bucket["max"]:
            return bucket
    return TENURE_BUCKETS[-1]  # veteran fallback


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class EmotionalSignature:
    """Aggregated emotional pattern for a group of staff."""
    avg_mood: float              # 1-5 average
    safe_rate: float             # 0-1 (% of days felt safe)
    fair_rate: float             # 0-1 (% of days felt fair)
    respected_rate: float        # 0-1 (% of days felt respected)
    
    # Trends (change over lookback window, negative = declining)
    mood_trend: float
    safe_trend: float
    fair_trend: float
    respected_trend: float
    
    # Sample info
    n_staff: int
    n_observations: int


@dataclass
class QuitterSignature:
    """Complete signature comparing quitters vs stayers for a tenure bucket."""
    bucket_name: str
    bucket_label: str
    
    quitter: EmotionalSignature
    stayer: EmotionalSignature
    
    # Gaps (positive = quitters are worse)
    mood_gap: float           # stayer_mood - quitter_mood
    safe_gap: float           # stayer_safe_rate - quitter_safe_rate  
    fair_gap: float           # stayer_fair_rate - quitter_fair_rate
    respected_gap: float      # stayer_respected_rate - quitter_respected_rate
    
    # Which signal is most predictive
    primary_signal: str
    signal_strength: float    # 0-1, how different quitters are from stayers


@dataclass 
class FlightRiskScore:
    """Flight risk assessment for an individual staff member."""
    staff_id: str
    score: int                 # 0-100
    risk_level: str            # "low", "moderate", "elevated", "high", "critical"
    tenure_days: int
    tenure_bucket: str
    
    # Which factors are contributing
    primary_concern: str       # e.g., "declining mood", "fairness issues"
    contributing_factors: List[str]
    
    # Raw metrics
    current_mood: float
    safe_rate: float
    fair_rate: float
    respected_rate: float
    mood_trend: float


# =============================================================================
# SIGNATURE EXTRACTION - Run once to learn patterns from synthetic data
# =============================================================================

def extract_signatures(supabase_client, lookback_days: int = 14) -> Dict[str, QuitterSignature]:
    """
    Extract quitter signatures from synthetic network data.
    
    Analyzes 3,500+ exits to learn what emotional patterns precede departure
    at each tenure stage.
    
    Args:
        supabase_client: Initialized Supabase client
        lookback_days: Days of history to analyze before exit (default 14)
        
    Returns:
        Dict mapping bucket_name to QuitterSignature
    """
    print("Extracting quitter signatures from synthetic network...")
    
    # Get all staff with pagination (Supabase default limit is 1000)
    all_staff = {}
    offset = 0
    batch_size = 1000
    
    while True:
        staff_response = supabase_client.table("synthetic_staff_master") \
            .select("staff_id, exit_day, final_persona, total_days") \
            .range(offset, offset + batch_size - 1) \
            .execute()
        
        if not staff_response.data:
            break
            
        for row in staff_response.data:
            all_staff[row["staff_id"]] = row
        
        if len(staff_response.data) < batch_size:
            break
            
        offset += batch_size
    
    print(f"  Loaded {len(all_staff)} total staff")
    
    quitters = {sid: data for sid, data in all_staff.items() 
                if data["final_persona"] == "exit"}
    stayers = {sid: data for sid, data in all_staff.items() 
               if data["final_persona"] != "exit"}
    
    print(f"  Total quitters: {len(quitters)}")
    print(f"  Total stayers: {len(stayers)}")
    
    signatures = {}
    
    for bucket in TENURE_BUCKETS:
        bucket_name = bucket["name"]
        print(f"\nAnalyzing {bucket['label']} (days {bucket['min']}-{bucket['max']})...")
        
        # Find quitters who exited in this tenure range
        bucket_quitters = [
            sid for sid, data in quitters.items()
            if bucket["min"] <= data["exit_day"] <= bucket["max"]
        ]
        
        # Find stayers with enough tenure for comparison
        bucket_stayers = [
            sid for sid, data in stayers.items()
            if data["total_days"] >= bucket["max"]
        ]
        
        print(f"  Quitters in bucket: {len(bucket_quitters)}")
        print(f"  Stayers for comparison: {len(bucket_stayers)}")
        
        if len(bucket_quitters) < 10:
            print(f"  Skipping - insufficient data")
            continue
        
        # Extract signatures
        quitter_sig = _extract_group_signature(
            supabase_client,
            staff_ids=bucket_quitters,
            staff_data=quitters,
            is_quitter=True,
            lookback_days=lookback_days,
            bucket=bucket,
        )
        
        stayer_sig = _extract_group_signature(
            supabase_client,
            staff_ids=bucket_stayers[:500],  # Sample to avoid huge queries
            staff_data=stayers,
            is_quitter=False,
            lookback_days=lookback_days,
            bucket=bucket,
        )
        
        if not quitter_sig or not stayer_sig:
            print(f"  Skipping - couldn't extract signatures")
            continue
        
        # Calculate gaps (positive = quitters are worse)
        mood_gap = stayer_sig.avg_mood - quitter_sig.avg_mood
        safe_gap = stayer_sig.safe_rate - quitter_sig.safe_rate
        fair_gap = stayer_sig.fair_rate - quitter_sig.fair_rate
        respected_gap = stayer_sig.respected_rate - quitter_sig.respected_rate
        
        # Find primary signal
        gaps = {
            "mood": mood_gap,
            "safety": safe_gap,
            "fairness": fair_gap,
            "respect": respected_gap,
        }
        primary_signal = max(gaps, key=lambda k: abs(gaps[k]))
        
        # Signal strength: normalized gap (0.5 gap on mood or 0.2 on rates is strong)
        max_gap = max(abs(mood_gap) / 2.0, abs(safe_gap) / 0.3, 
                      abs(fair_gap) / 0.3, abs(respected_gap) / 0.3)
        signal_strength = min(1.0, max_gap)
        
        signatures[bucket_name] = QuitterSignature(
            bucket_name=bucket_name,
            bucket_label=bucket["label"],
            quitter=quitter_sig,
            stayer=stayer_sig,
            mood_gap=mood_gap,
            safe_gap=safe_gap,
            fair_gap=fair_gap,
            respected_gap=respected_gap,
            primary_signal=primary_signal,
            signal_strength=signal_strength,
        )
        
        print(f"  Primary signal: {primary_signal}")
        print(f"  Quitter mood: {quitter_sig.avg_mood:.2f}, Stayer: {stayer_sig.avg_mood:.2f}")
        print(f"  Quitter fair_rate: {quitter_sig.fair_rate:.1%}, Stayer: {stayer_sig.fair_rate:.1%}")
    
    return signatures


def _extract_group_signature(
    supabase_client,
    staff_ids: List[str],
    staff_data: Dict[str, Any],
    is_quitter: bool,
    lookback_days: int,
    bucket: Dict[str, Any],
) -> Optional[EmotionalSignature]:
    """Extract emotional signature for a group of staff."""
    
    if not staff_ids:
        return None
    
    all_moods = []
    all_safe = []
    all_fair = []
    all_respected = []
    
    # For trend: early half vs late half of lookback
    early_moods = []
    late_moods = []
    early_safe = []
    late_safe = []
    early_fair = []
    late_fair = []
    early_respected = []
    late_respected = []
    
    # Query in batches
    batch_size = 100
    
    for i in range(0, min(len(staff_ids), 500), batch_size):
        batch_ids = staff_ids[i:i+batch_size]
        
        # Determine tenure range to query
        if is_quitter:
            # Get data leading up to exit
            min_tenure = max(0, bucket["min"] - lookback_days)
            max_tenure = bucket["max"]
        else:
            # Get data at equivalent tenure for fair comparison
            min_tenure = bucket["min"]
            max_tenure = bucket["max"]
        
        response = supabase_client.table("synthetic_daily_emotions") \
            .select("staff_id, tenure_days, mood_emoji, felt_safe, felt_fair, felt_respected") \
            .in_("staff_id", batch_ids) \
            .gte("tenure_days", min_tenure) \
            .lte("tenure_days", max_tenure) \
            .execute()
        
        for row in response.data:
            sid = row["staff_id"]
            tenure = row["tenure_days"]
            
            mood = row["mood_emoji"]
            safe = 1 if row["felt_safe"] else 0
            fair = 1 if row["felt_fair"] else 0
            respected = 1 if row["felt_respected"] else 0
            
            if is_quitter:
                exit_day = staff_data[sid]["exit_day"]
                days_before_exit = exit_day - tenure
                
                if days_before_exit > lookback_days or days_before_exit < 0:
                    continue
                
                all_moods.append(mood)
                all_safe.append(safe)
                all_fair.append(fair)
                all_respected.append(respected)
                
                # Split into early/late for trend
                if days_before_exit >= lookback_days // 2:
                    early_moods.append(mood)
                    early_safe.append(safe)
                    early_fair.append(fair)
                    early_respected.append(respected)
                else:
                    late_moods.append(mood)
                    late_safe.append(safe)
                    late_fair.append(fair)
                    late_respected.append(respected)
            else:
                # Stayer: use data at equivalent tenure
                all_moods.append(mood)
                all_safe.append(safe)
                all_fair.append(fair)
                all_respected.append(respected)
                
                bucket_mid = (bucket["min"] + bucket["max"]) / 2
                if tenure < bucket_mid:
                    early_moods.append(mood)
                    early_safe.append(safe)
                    early_fair.append(fair)
                    early_respected.append(respected)
                else:
                    late_moods.append(mood)
                    late_safe.append(safe)
                    late_fair.append(fair)
                    late_respected.append(respected)
    
    if len(all_moods) < 10:
        return None
    
    def safe_mean(lst):
        return statistics.mean(lst) if lst else 0.0
    
    def safe_trend(late_lst, early_lst):
        if len(late_lst) < 5 or len(early_lst) < 5:
            return 0.0
        return safe_mean(late_lst) - safe_mean(early_lst)
    
    return EmotionalSignature(
        avg_mood=safe_mean(all_moods),
        safe_rate=safe_mean(all_safe),
        fair_rate=safe_mean(all_fair),
        respected_rate=safe_mean(all_respected),
        mood_trend=safe_trend(late_moods, early_moods),
        safe_trend=safe_trend(late_safe, early_safe),
        fair_trend=safe_trend(late_fair, early_fair),
        respected_trend=safe_trend(late_respected, early_respected),
        n_staff=len(staff_ids),
        n_observations=len(all_moods),
    )


# =============================================================================
# FLIGHT RISK SCORING - Run against organic staff
# =============================================================================

def score_staff_flight_risk(
    supabase_client,
    restaurant_id: int,
    signatures: Dict[str, QuitterSignature],
    lookback_days: int = 14,
) -> List[FlightRiskScore]:
    """
    Score all active staff at a restaurant for flight risk.
    
    Compares each staff member's recent check-in trajectory to 
    quitter signatures at their tenure stage.
    
    Args:
        supabase_client: Initialized Supabase client
        restaurant_id: Restaurant to analyze
        signatures: Pre-extracted quitter signatures from synthetic network
        lookback_days: Days of check-in history to consider
        
    Returns:
        List of FlightRiskScore objects, sorted by score descending
    """
    
    # Get active staff and their tenure
    staff_response = supabase_client.table("staff") \
        .select("staff_id, full_name, hire_date") \
        .eq("restaurant_id", restaurant_id) \
        .eq("status", "Active") \
        .execute()
    
    if not staff_response.data:
        return []
    
    from datetime import date, datetime
    today = date.today()
    
    scores = []
    
    for staff in staff_response.data:
        staff_id = staff["staff_id"]
        
        # Calculate tenure
        if staff.get("hire_date"):
            if isinstance(staff["hire_date"], str):
                hire_date = datetime.fromisoformat(staff["hire_date"].replace("Z", "")).date()
            else:
                hire_date = staff["hire_date"]
            tenure_days = (today - hire_date).days
        else:
            tenure_days = 30  # default assumption
        
        # Get their bucket
        bucket = get_tenure_bucket(tenure_days)
        bucket_name = bucket["name"]
        
        # Get their recent check-ins
        checkins_response = supabase_client.table("sse_daily_checkins") \
            .select("mood_emoji, felt_safe, felt_fair, felt_respected, checkin_date") \
            .eq("staff_id", staff_id) \
            .order("checkin_date", desc=True) \
            .limit(lookback_days) \
            .execute()
        
        checkins = checkins_response.data
        
        if len(checkins) < 3:
            # Not enough data to score
            continue
        
        # Calculate their metrics
        moods = [c["mood_emoji"] for c in checkins]
        safes = [1 if c["felt_safe"] else 0 for c in checkins]
        fairs = [1 if c["felt_fair"] else 0 for c in checkins]
        respecteds = [1 if c["felt_respected"] else 0 for c in checkins]
        
        avg_mood = statistics.mean(moods)
        safe_rate = statistics.mean(safes)
        fair_rate = statistics.mean(fairs)
        respected_rate = statistics.mean(respecteds)
        
        # Calculate trend (recent vs older, checkins are desc sorted)
        mid = len(checkins) // 2
        recent = checkins[:mid] if mid > 0 else checkins
        older = checkins[mid:] if mid > 0 else []
        
        if recent and older:
            recent_mood = statistics.mean([c["mood_emoji"] for c in recent])
            older_mood = statistics.mean([c["mood_emoji"] for c in older])
            mood_trend = recent_mood - older_mood
        else:
            mood_trend = 0.0
        
        # Score against signature
        score, risk_level, primary_concern, factors = _calculate_flight_score(
            avg_mood=avg_mood,
            safe_rate=safe_rate,
            fair_rate=fair_rate,
            respected_rate=respected_rate,
            mood_trend=mood_trend,
            tenure_days=tenure_days,
            signatures=signatures,
            bucket_name=bucket_name,
        )
        
        scores.append(FlightRiskScore(
            staff_id=staff_id,
            score=score,
            risk_level=risk_level,
            tenure_days=tenure_days,
            tenure_bucket=bucket["label"],
            primary_concern=primary_concern,
            contributing_factors=factors,
            current_mood=avg_mood,
            safe_rate=safe_rate,
            fair_rate=fair_rate,
            respected_rate=respected_rate,
            mood_trend=mood_trend,
        ))
    
    # Sort by score descending (highest risk first)
    scores.sort(key=lambda x: x.score, reverse=True)
    
    return scores


def _calculate_flight_score(
    avg_mood: float,
    safe_rate: float,
    fair_rate: float,
    respected_rate: float,
    mood_trend: float,
    tenure_days: int,
    signatures: Dict[str, QuitterSignature],
    bucket_name: str,
) -> tuple[int, str, str, List[str]]:
    """
    Calculate flight risk score by comparing to quitter signature.
    
    Returns: (score 0-100, risk_level, primary_concern, contributing_factors)
    """
    
    # Get signature for this tenure bucket
    sig = signatures.get(bucket_name)
    
    if not sig:
        # No signature for this bucket, use general heuristics
        return _heuristic_score(avg_mood, safe_rate, fair_rate, respected_rate, mood_trend)
    
    quitter = sig.quitter
    stayer = sig.stayer
    
    # Calculate how close this person is to quitter vs stayer profile
    # Score each dimension: 0 = like stayer, 100 = like quitter
    
    def dimension_score(value: float, quitter_val: float, stayer_val: float) -> float:
        """0 = matches stayer, 100 = matches quitter"""
        if abs(stayer_val - quitter_val) < 0.01:
            return 50  # No difference in this dimension
        
        # Where does this value fall between stayer and quitter?
        position = (stayer_val - value) / (stayer_val - quitter_val)
        return max(0, min(100, position * 100))
    
    mood_score = dimension_score(avg_mood, quitter.avg_mood, stayer.avg_mood)
    safe_score = dimension_score(safe_rate, quitter.safe_rate, stayer.safe_rate)
    fair_score = dimension_score(fair_rate, quitter.fair_rate, stayer.fair_rate)
    respect_score = dimension_score(respected_rate, quitter.respected_rate, stayer.respected_rate)
    
    # Weight by signal strength at this tenure bucket
    weights = {
        "mood": 0.35,
        "safety": 0.15,
        "fairness": 0.25,
        "respect": 0.25,
    }
    
    # Boost weight of primary signal for this bucket
    weights[sig.primary_signal] += 0.15
    total_weight = sum(weights.values())
    weights = {k: v/total_weight for k, v in weights.items()}
    
    raw_score = (
        mood_score * weights["mood"] +
        safe_score * weights["safety"] +
        fair_score * weights["fairness"] +
        respect_score * weights["respect"]
    )
    
    # Trend modifier: declining mood adds risk
    if mood_trend < -0.3:
        raw_score += 10
    elif mood_trend < -0.5:
        raw_score += 20
    
    # Tenure modifier: early tenure is inherently riskier
    if tenure_days <= 30:
        raw_score += 10
    elif tenure_days <= 90:
        raw_score += 5
    
    final_score = int(max(0, min(100, raw_score)))
    
    # Determine risk level
    if final_score >= 80:
        risk_level = "critical"
    elif final_score >= 65:
        risk_level = "high"
    elif final_score >= 50:
        risk_level = "elevated"
    elif final_score >= 35:
        risk_level = "moderate"
    else:
        risk_level = "low"
    
    # Identify concerns
    factors = []
    primary_concern = "general trajectory"
    
    concerns = [
        (mood_score, "declining mood", "mood"),
        (safe_score, "safety concerns", "safety"),
        (fair_score, "fairness issues", "fairness"),
        (respect_score, "feeling disrespected", "respect"),
    ]
    concerns.sort(key=lambda x: x[0], reverse=True)
    
    if concerns[0][0] >= 50:
        primary_concern = concerns[0][1]
    
    for score, label, _ in concerns:
        if score >= 40:
            factors.append(label)
    
    if mood_trend < -0.3:
        factors.append("mood trending down")
    
    return final_score, risk_level, primary_concern, factors


def _heuristic_score(
    avg_mood: float,
    safe_rate: float,
    fair_rate: float,
    respected_rate: float,
    mood_trend: float,
) -> tuple[int, str, str, List[str]]:
    """Fallback scoring when no signature available."""
    
    score = 0
    factors = []
    
    # Mood (1-5 scale)
    if avg_mood <= 2.0:
        score += 40
        factors.append("critically low mood")
    elif avg_mood <= 2.5:
        score += 25
        factors.append("low mood")
    elif avg_mood <= 3.0:
        score += 10
    
    # Safety
    if safe_rate < 0.5:
        score += 25
        factors.append("safety concerns")
    elif safe_rate < 0.7:
        score += 10
    
    # Fairness
    if fair_rate < 0.5:
        score += 20
        factors.append("fairness issues")
    elif fair_rate < 0.7:
        score += 10
    
    # Respect
    if respected_rate < 0.5:
        score += 20
        factors.append("feeling disrespected")
    elif respected_rate < 0.7:
        score += 10
    
    # Trend
    if mood_trend < -0.5:
        score += 15
        factors.append("mood trending down")
    elif mood_trend < -0.3:
        score += 8
    
    final_score = min(100, score)
    
    if final_score >= 80:
        risk_level = "critical"
    elif final_score >= 65:
        risk_level = "high"
    elif final_score >= 50:
        risk_level = "elevated"
    elif final_score >= 35:
        risk_level = "moderate"
    else:
        risk_level = "low"
    
    primary_concern = factors[0] if factors else "general trajectory"
    
    return final_score, risk_level, primary_concern, factors


# =============================================================================
# NETWORK BENCHMARK - Compare restaurant to network averages
# =============================================================================

def calculate_network_percentile(
    supabase_client,
    restaurant_id: int,
    metric: str = "mood",
) -> Dict[str, Any]:
    """
    Calculate where a restaurant ranks against the synthetic network.
    
    Args:
        supabase_client: Initialized Supabase client
        restaurant_id: Restaurant to benchmark
        metric: "mood", "safety", "fairness", or "respect"
        
    Returns:
        Dict with percentile, restaurant_value, network_average, network_median
    """
    
    # Get restaurant's recent metrics (last 30 days)
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=30)
    
    restaurant_response = supabase_client.table("sse_daily_checkins") \
        .select("mood_emoji, felt_safe, felt_fair, felt_respected") \
        .eq("restaurant_id", restaurant_id) \
        .gte("checkin_date", cutoff.isoformat()) \
        .execute()
    
    if not restaurant_response.data or len(restaurant_response.data) < 5:
        return {
            "percentile": None,
            "restaurant_value": None,
            "network_average": None,
            "error": "Insufficient restaurant data"
        }
    
    # Calculate restaurant's metric
    if metric == "mood":
        restaurant_value = statistics.mean([r["mood_emoji"] for r in restaurant_response.data])
    elif metric == "safety":
        restaurant_value = statistics.mean([1 if r["felt_safe"] else 0 for r in restaurant_response.data])
    elif metric == "fairness":
        restaurant_value = statistics.mean([1 if r["felt_fair"] else 0 for r in restaurant_response.data])
    elif metric == "respect":
        restaurant_value = statistics.mean([1 if r["felt_respected"] else 0 for r in restaurant_response.data])
    else:
        return {"error": f"Unknown metric: {metric}"}
    
    # Get network averages per restaurant
    network_response = supabase_client.table("synthetic_daily_emotions") \
        .select("restaurant_id, mood_emoji, felt_safe, felt_fair, felt_respected") \
        .execute()
    
    # Group by restaurant
    from collections import defaultdict
    restaurant_metrics = defaultdict(list)
    
    for row in network_response.data:
        rid = row["restaurant_id"]
        if metric == "mood":
            restaurant_metrics[rid].append(row["mood_emoji"])
        elif metric == "safety":
            restaurant_metrics[rid].append(1 if row["felt_safe"] else 0)
        elif metric == "fairness":
            restaurant_metrics[rid].append(1 if row["felt_fair"] else 0)
        elif metric == "respect":
            restaurant_metrics[rid].append(1 if row["felt_respected"] else 0)
    
    # Calculate average per restaurant
    network_averages = [statistics.mean(vals) for vals in restaurant_metrics.values() if vals]
    
    if not network_averages:
        return {"error": "No network data"}
    
    # Calculate percentile
    better_than = sum(1 for avg in network_averages if restaurant_value > avg)
    percentile = int((better_than / len(network_averages)) * 100)
    
    return {
        "percentile": percentile,
        "restaurant_value": round(restaurant_value, 2),
        "network_average": round(statistics.mean(network_averages), 2),
        "network_median": round(statistics.median(network_averages), 2),
        "network_size": len(network_averages),
    }


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def print_signature_report(signatures: Dict[str, QuitterSignature]) -> None:
    """Print human-readable signature report."""
    
    print("\n" + "=" * 70)
    print("QUITTER SIGNATURE REPORT")
    print("=" * 70)
    
    for bucket_name, sig in signatures.items():
        print(f"\n{'─' * 70}")
        print(f"{sig.bucket_label.upper()}")
        print(f"{'─' * 70}")
        
        print(f"\n  PRIMARY SIGNAL: {sig.primary_signal.upper()}")
        print(f"  Signal Strength: {sig.signal_strength:.0%}")
        
        q = sig.quitter
        s = sig.stayer
        
        print(f"\n  Quitters ({q.n_staff} staff, {q.n_observations} check-ins):")
        print(f"    Mood:     {q.avg_mood:.2f} (trend: {q.mood_trend:+.2f})")
        print(f"    Safe:     {q.safe_rate:.0%}")
        print(f"    Fair:     {q.fair_rate:.0%}")
        print(f"    Respected:{q.respected_rate:.0%}")
        
        print(f"\n  Stayers ({s.n_staff} staff, {s.n_observations} check-ins):")
        print(f"    Mood:     {s.avg_mood:.2f} (trend: {s.mood_trend:+.2f})")
        print(f"    Safe:     {s.safe_rate:.0%}")
        print(f"    Fair:     {s.fair_rate:.0%}")
        print(f"    Respected:{s.respected_rate:.0%}")
        
        print(f"\n  GAPS (stayer - quitter):")
        print(f"    Mood:     {sig.mood_gap:+.2f}")
        print(f"    Safe:     {sig.safe_gap:+.0%}")
        print(f"    Fair:     {sig.fair_gap:+.0%}")
        print(f"    Respected:{sig.respected_gap:+.0%}")
    
    print("\n" + "=" * 70)


def signatures_to_dict(signatures: Dict[str, QuitterSignature]) -> Dict[str, Any]:
    """Convert signatures to JSON-serializable dict for storage/caching."""
    
    output = {}
    for bucket_name, sig in signatures.items():
        output[bucket_name] = {
            "bucket_label": sig.bucket_label,
            "primary_signal": sig.primary_signal,
            "signal_strength": sig.signal_strength,
            "quitter": {
                "avg_mood": sig.quitter.avg_mood,
                "safe_rate": sig.quitter.safe_rate,
                "fair_rate": sig.quitter.fair_rate,
                "respected_rate": sig.quitter.respected_rate,
                "mood_trend": sig.quitter.mood_trend,
                "n_staff": sig.quitter.n_staff,
                "n_observations": sig.quitter.n_observations,
            },
            "stayer": {
                "avg_mood": sig.stayer.avg_mood,
                "safe_rate": sig.stayer.safe_rate,
                "fair_rate": sig.stayer.fair_rate,
                "respected_rate": sig.stayer.respected_rate,
                "mood_trend": sig.stayer.mood_trend,
                "n_staff": sig.stayer.n_staff,
                "n_observations": sig.stayer.n_observations,
            },
            "gaps": {
                "mood": sig.mood_gap,
                "safe": sig.safe_gap,
                "fair": sig.fair_gap,
                "respected": sig.respected_gap,
            }
        }
    return output