"""
SCHEDULE ANALYSIS SERVICE
=========================
Analyzes parsed schedules for fairness, fatigue, and preference conflicts.

The three-layer model:
1. AVAILABILITY - Hard constraints (violations = critical)
2. HIRED ROLE - Contract expectations (no penalty for staying within)
3. PREFERENCES - Soft signals (drift = retention risk)

Output matches SCHEDULE_DATA structure from frontend.
"""

import logging
from datetime import datetime, date, timedelta, time
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict

from supabase import create_client, Client
from config.settings import SUPABASE_URL, SUPABASE_KEY

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Fatigue thresholds
MIN_TURNAROUND_HOURS = 10          # Less than this = violation
CONSECUTIVE_CLOSE_THRESHOLD = 3    # 3+ closes in a row = high risk
CONSECUTIVE_DAYS_THRESHOLD = 6     # 6+ days straight = critical
MAX_WEEKLY_HOURS_WARNING = 45      # Flag if exceeding

# Fairness thresholds
WEEKEND_CONCENTRATION_THRESHOLD = 0.60  # Top 3 shouldn't have >60% of weekend shifts
CLOSING_IMBALANCE_THRESHOLD = 1.5       # >1.5x peer average = imbalanced

# Scoring weights
STABILITY_WEIGHTS = {
    "fairness": 0.30,
    "fatigue": 0.35,
    "preference": 0.25,
    "historical": 0.10
}


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ANALYSIS FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

async def analyze_schedule(
    shifts: List[Dict],
    organization_id: int,
    week_of: str,
    manager_notes: str = ""
) -> Dict[str, Any]:
    """
    Full analysis of a parsed schedule.
    
    Args:
        shifts: List of normalized shifts from parser
        organization_id: Restaurant ID
        week_of: Start date of week (YYYY-MM-DD)
        manager_notes: Optional context from manager
    
    Returns:
        Complete analysis matching SCHEDULE_DATA structure
    """
    # Fetch supporting data
    work_profiles = get_work_profiles(organization_id)
    staff_lookup = get_staff_lookup(organization_id)
    historical_shifts = get_historical_shifts(organization_id, week_of, weeks_back=4)
    
    # Build profile lookup
    profile_lookup = {p["staff_id"]: p for p in work_profiles}
    
    # Run all analyses
    fairness_result = analyze_fairness(shifts, historical_shifts, staff_lookup)
    fatigue_result = analyze_fatigue(shifts, historical_shifts, profile_lookup)
    preference_result = analyze_preferences(shifts, profile_lookup, staff_lookup)
    
    # Calculate overall scores
    fairness_score = fairness_result["score"]
    fatigue_risk = fatigue_result["risk_score"]
    preference_score = preference_result["score"]
    
    # Stability score (fatigue inverted since lower = better)
    stability_score = int(
        fairness_score * STABILITY_WEIGHTS["fairness"] +
        (100 - fatigue_risk) * STABILITY_WEIGHTS["fatigue"] +
        preference_score * STABILITY_WEIGHTS["preference"] +
        75 * STABILITY_WEIGHTS["historical"]  # Default historical score
    )
    
    # Generate priority fixes
    priority_fixes = generate_priority_fixes(
        fairness_result, fatigue_result, preference_result, staff_lookup
    )
    
    # Generate staff impact summary
    staff_impact = generate_staff_impact(
        shifts, fairness_result, fatigue_result, preference_result, 
        profile_lookup, staff_lookup
    )
    
    # Emotional fallout predictions
    emotional_fallout = predict_emotional_fallout(
        shifts, preference_result, fatigue_result, profile_lookup, staff_lookup
    )
    
    # SSE events to auto-create
    sse_events = generate_sse_events(
        fatigue_result, preference_result, staff_lookup
    )
    
    # Predicted reaction
    predicted_reaction = calculate_predicted_reaction(
        fairness_score, fatigue_risk, preference_score, len(priority_fixes)
    )
    
    # Week metadata
    week_start = datetime.strptime(week_of, "%Y-%m-%d")
    week_end = week_start + timedelta(days=6)
    week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"
    
    # Count unique staff
    unique_staff = set(s["staff_id"] for s in shifts)
    total_hours = sum(calculate_shift_hours(s) for s in shifts)
    
    return {
        "currentDraft": {
            "id": f"SCHED-{week_start.strftime('%Y-W%W')}",
            "weekOf": week_of,
            "weekLabel": week_label,
            "uploadedAt": datetime.utcnow().isoformat() + "Z",
            "status": "analyzed",
            "totalShifts": len(shifts),
            "totalHours": round(total_hours, 1),
            "staffScheduled": len(unique_staff),
            "managerNotes": manager_notes
        },
        
        "analysis": {
            "scores": {
                "stabilityScore": stability_score,
                "fairnessScore": fairness_score,
                "preferenceAlignment": preference_score,
                "fatigueRisk": fatigue_risk
            },
            "interpretations": {
                "stability": get_stability_interpretation(stability_score, len(priority_fixes)),
                "fairness": fairness_result["interpretation"],
                "preference": preference_result["interpretation"],
                "fatigue": fatigue_result["interpretation"]
            },
            "predictedReaction": predicted_reaction,
            "predictedSmmMovement": calculate_smm_prediction(stability_score)
        },
        
        "priorityFixes": priority_fixes,
        "staffImpact": staff_impact,
        "fairnessAnalysis": fairness_result["details"],
        "fatigueAnalysis": fatigue_result["details"],
        "preferenceConflicts": preference_result["details"],
        "emotionalFallout": emotional_fallout,
        "sseEvents": sse_events,
        
        "historicalComparison": {
            "lastWeekScore": 75,  # TODO: Pull from actual history
            "fourWeekAvg": 73,
            "trend": "stable",
            "trendDetail": "Consistent with recent weeks"
        }
    }


# ═══════════════════════════════════════════════════════════════════════════
# FAIRNESS ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

def analyze_fairness(
    shifts: List[Dict], 
    historical_shifts: List[Dict],
    staff_lookup: Dict
) -> Dict[str, Any]:
    """
    Analyze schedule fairness across multiple dimensions.
    """
    # Group shifts by staff
    staff_shifts = defaultdict(list)
    for shift in shifts:
        staff_shifts[shift["staff_id"]].append(shift)
    
    # Weekend equity (Fri, Sat dinner shifts)
    weekend_equity = calculate_weekend_equity(shifts, staff_lookup)
    
    # Opening/closing distribution
    opening_dist = calculate_shift_type_distribution(shifts, "AM", staff_lookup)
    closing_dist = calculate_shift_type_distribution(shifts, "Close", staff_lookup)
    
    # Hours variance
    hours_variance = calculate_hours_variance(staff_shifts)
    
    # Prime shift access (Fri/Sat PM)
    prime_access = calculate_prime_shift_access(shifts, staff_lookup)
    
    # Calculate overall fairness score
    scores = [
        weekend_equity["score"],
        opening_dist["score"],
        closing_dist["score"],
        hours_variance["score"],
        prime_access["score"]
    ]
    overall_score = int(sum(scores) / len(scores))
    
    # Find top imbalances
    imbalances = []
    if weekend_equity["score"] < 70:
        imbalances.append({
            "metric": "Weekend prime shifts",
            "issue": weekend_equity["issue"],
            "severity": "high" if weekend_equity["score"] < 60 else "medium"
        })
    if closing_dist["score"] < 70:
        imbalances.append({
            "metric": "Closing frequency",
            "issue": closing_dist["issue"],
            "severity": "high" if closing_dist["score"] < 60 else "medium"
        })
    if hours_variance["score"] < 70:
        imbalances.append({
            "metric": "Hours distribution",
            "issue": hours_variance["issue"],
            "severity": "medium"
        })
    
    # Interpretation
    if overall_score >= 80:
        interpretation = "Well balanced schedule. Minor adjustments optional."
    elif overall_score >= 70:
        interpretation = "Some imbalances detected. Review flagged items."
    elif overall_score >= 60:
        interpretation = "Significant fairness issues. Multiple staff may feel overlooked."
    else:
        interpretation = "Critical fairness problems. Resentment likely without changes."
    
    return {
        "score": overall_score,
        "interpretation": interpretation,
        "details": {
            "overallScore": overall_score,
            "breakdown": {
                "weekendEquity": {
                    "score": weekend_equity["score"],
                    "status": get_status(weekend_equity["score"]),
                    "detail": weekend_equity["detail"]
                },
                "openingClosing": {
                    "score": int((opening_dist["score"] + closing_dist["score"]) / 2),
                    "status": get_status(int((opening_dist["score"] + closing_dist["score"]) / 2)),
                    "detail": closing_dist["detail"]
                },
                "preferredShifts": {
                    "score": prime_access["score"],
                    "status": get_status(prime_access["score"]),
                    "detail": prime_access["detail"]
                },
                "hoursVariance": {
                    "score": hours_variance["score"],
                    "status": get_status(hours_variance["score"]),
                    "detail": hours_variance["detail"]
                }
            },
            "topImbalances": imbalances
        },
        "issues": imbalances
    }


def calculate_weekend_equity(shifts: List[Dict], staff_lookup: Dict) -> Dict:
    """Calculate how evenly weekend shifts are distributed."""
    weekend_days = ["Friday", "Saturday"]
    
    # Count weekend PM shifts per person
    weekend_counts = defaultdict(int)
    total_weekend = 0
    
    for shift in shifts:
        shift_date = datetime.strptime(shift["date"], "%Y-%m-%d")
        day_name = shift_date.strftime("%A")
        
        if day_name in weekend_days and is_pm_shift(shift):
            weekend_counts[shift["staff_id"]] += 1
            total_weekend += 1
    
    if total_weekend == 0:
        return {"score": 100, "detail": "No weekend shifts to analyze", "issue": None}
    
    # Check concentration
    sorted_counts = sorted(weekend_counts.values(), reverse=True)
    top_3_total = sum(sorted_counts[:3]) if len(sorted_counts) >= 3 else sum(sorted_counts)
    concentration = top_3_total / total_weekend if total_weekend > 0 else 0
    
    # Score based on concentration
    if concentration <= 0.50:
        score = 90
    elif concentration <= 0.60:
        score = 75
    elif concentration <= 0.70:
        score = 60
    else:
        score = 45
    
    # Find who has the most
    if weekend_counts:
        top_staff_id = max(weekend_counts, key=weekend_counts.get)
        top_name = staff_lookup.get(top_staff_id, {}).get("full_name", "Unknown")
        top_count = weekend_counts[top_staff_id]
        
        issue = f"Top 3 staff get {int(concentration*100)}% of weekend shifts"
        detail = f"{top_name} has {top_count} of {total_weekend} weekend shifts"
    else:
        issue = None
        detail = "Weekend shifts well distributed"
    
    return {"score": score, "detail": detail, "issue": issue}


def calculate_shift_type_distribution(
    shifts: List[Dict], 
    shift_type: str,
    staff_lookup: Dict
) -> Dict:
    """Calculate distribution of opening or closing shifts."""
    type_counts = defaultdict(int)
    total_type = 0
    
    for shift in shifts:
        is_target = (
            (shift_type == "AM" and is_am_shift(shift)) or
            (shift_type == "Close" and is_close_shift(shift))
        )
        if is_target:
            type_counts[shift["staff_id"]] += 1
            total_type += 1
    
    if total_type == 0:
        return {"score": 100, "detail": f"No {shift_type} shifts to analyze", "issue": None}
    
    # Calculate average and find outliers
    if len(type_counts) > 0:
        avg_count = total_type / len(type_counts)
        max_count = max(type_counts.values())
        max_staff_id = max(type_counts, key=type_counts.get)
        max_name = staff_lookup.get(max_staff_id, {}).get("full_name", "Unknown")
        
        # Check for imbalance
        ratio = max_count / avg_count if avg_count > 0 else 1
        
        if ratio <= 1.3:
            score = 90
        elif ratio <= 1.5:
            score = 75
        elif ratio <= 2.0:
            score = 60
        else:
            score = 45
        
        if ratio > 1.5:
            issue = f"{max_name} has {int((ratio-1)*100)}% more {shift_type.lower()}s than average"
        else:
            issue = None
        
        detail = f"{max_name} has {max_count} of {total_type} {shift_type.lower()} shifts"
    else:
        score = 100
        issue = None
        detail = f"{shift_type} shifts well distributed"
    
    return {"score": score, "detail": detail, "issue": issue}


def calculate_hours_variance(staff_shifts: Dict[str, List]) -> Dict:
    """Calculate variance in total hours across staff."""
    if not staff_shifts:
        return {"score": 100, "detail": "No shifts to analyze", "issue": None}
    
    hours_per_staff = {}
    for staff_id, shifts in staff_shifts.items():
        total = sum(calculate_shift_hours(s) for s in shifts)
        hours_per_staff[staff_id] = total
    
    if not hours_per_staff:
        return {"score": 100, "detail": "No hours to analyze", "issue": None}
    
    max_hours = max(hours_per_staff.values())
    min_hours = min(hours_per_staff.values())
    gap = max_hours - min_hours
    
    # Score based on gap
    if gap <= 8:
        score = 90
    elif gap <= 12:
        score = 75
    elif gap <= 18:
        score = 60
    else:
        score = 45
    
    detail = f"Hours range from {min_hours:.0f} to {max_hours:.0f} (gap: {gap:.0f})"
    issue = f"{gap:.0f} hour gap between highest and lowest" if gap > 12 else None
    
    return {"score": score, "detail": detail, "issue": issue}


def calculate_prime_shift_access(shifts: List[Dict], staff_lookup: Dict) -> Dict:
    """Calculate access to prime shifts (Fri/Sat PM)."""
    prime_days = ["Friday", "Saturday"]
    
    prime_counts = defaultdict(int)
    total_prime = 0
    
    for shift in shifts:
        shift_date = datetime.strptime(shift["date"], "%Y-%m-%d")
        day_name = shift_date.strftime("%A")
        
        if day_name in prime_days and is_pm_shift(shift):
            prime_counts[shift["staff_id"]] += 1
            total_prime += 1
    
    if total_prime == 0:
        return {"score": 100, "detail": "No prime shifts to analyze", "issue": None}
    
    # Count unique staff with prime access
    staff_with_prime = len(prime_counts)
    total_staff = len(set(s["staff_id"] for s in shifts))
    access_ratio = staff_with_prime / total_staff if total_staff > 0 else 0
    
    if access_ratio >= 0.6:
        score = 85
    elif access_ratio >= 0.4:
        score = 70
    elif access_ratio >= 0.25:
        score = 55
    else:
        score = 40
    
    detail = f"{staff_with_prime} of {total_staff} staff get prime shifts"
    issue = f"Only {int(access_ratio*100)}% of staff get prime shifts" if access_ratio < 0.5 else None
    
    return {"score": score, "detail": detail, "issue": issue}


# ═══════════════════════════════════════════════════════════════════════════
# FATIGUE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

def analyze_fatigue(
    shifts: List[Dict],
    historical_shifts: List[Dict],
    profile_lookup: Dict
) -> Dict[str, Any]:
    """
    Analyze fatigue risk across the schedule.
    """
    # Combine current and recent historical for streak detection
    all_shifts = historical_shifts + shifts
    
    # Group by staff
    staff_shifts = defaultdict(list)
    for shift in shifts:
        staff_shifts[shift["staff_id"]].append(shift)
    
    all_staff_shifts = defaultdict(list)
    for shift in all_shifts:
        all_staff_shifts[shift["staff_id"]].append(shift)
    
    # Analyze each staff member
    at_risk_staff = []
    turnaround_violations = []
    long_streaks = []
    
    for staff_id, week_shifts in staff_shifts.items():
        all_this_staff = sorted(all_staff_shifts[staff_id], key=lambda x: x["date"])
        week_shifts_sorted = sorted(week_shifts, key=lambda x: x["date"])
        
        # Calculate burnout factors
        burnout_score = 0
        factors = []
        
        # Check close-to-open turnarounds
        turnarounds = find_turnaround_violations(week_shifts_sorted)
        if turnarounds:
            burnout_score += 30
            factors.append(f"{len(turnarounds)} close-to-open turnaround(s)")
            turnaround_violations.extend(turnarounds)
        
        # Check consecutive closes
        close_streak = find_consecutive_closes(all_this_staff, week_shifts_sorted)
        if close_streak >= CONSECUTIVE_CLOSE_THRESHOLD:
            burnout_score += 25
            factors.append(f"{close_streak} closes in a row")
            long_streaks.append({
                "staff_id": staff_id,
                "type": "closing",
                "count": close_streak
            })
        
        # Check consecutive days worked
        day_streak = find_consecutive_days(all_this_staff)
        if day_streak >= CONSECUTIVE_DAYS_THRESHOLD:
            burnout_score += 35
            factors.append(f"{day_streak} consecutive days")
            long_streaks.append({
                "staff_id": staff_id,
                "type": "days",
                "count": day_streak
            })
        
        # Check weekly hours
        weekly_hours = sum(calculate_shift_hours(s) for s in week_shifts)
        if weekly_hours > MAX_WEEKLY_HOURS_WARNING:
            burnout_score += 15
            factors.append(f"{weekly_hours:.0f} hours this week")
        
        # Cap at 100
        burnout_score = min(burnout_score, 100)
        
        if burnout_score >= 30:
            profile = profile_lookup.get(staff_id, {})
            at_risk_staff.append({
                "staff_id": staff_id,
                "burnout_probability": burnout_score / 100,
                "factors": factors,
                "weekly_hours": weekly_hours
            })
    
    # Overall risk = weighted average of top risks
    if at_risk_staff:
        sorted_risks = sorted(at_risk_staff, key=lambda x: x["burnout_probability"], reverse=True)
        top_risks = sorted_risks[:3]
        overall_risk = int(sum(r["burnout_probability"] * 100 for r in top_risks) / len(top_risks))
    else:
        overall_risk = 15  # Baseline
    
    # Risk level
    if overall_risk <= 30:
        risk_level = "low"
        interpretation = "Fatigue risk is well managed."
    elif overall_risk <= 50:
        risk_level = "moderate"
        interpretation = "Some fatigue concerns. Monitor flagged staff."
    elif overall_risk <= 70:
        risk_level = "elevated"
        interpretation = f"Elevated burnout risk for {len(at_risk_staff)} staff member(s)."
    else:
        risk_level = "high"
        interpretation = "Critical fatigue levels. Immediate schedule adjustments recommended."
    
    return {
        "risk_score": overall_risk,
        "interpretation": interpretation,
        "details": {
            "overallRisk": overall_risk,
            "riskLevel": risk_level,
            "atRiskStaff": at_risk_staff,
            "turnaroundViolations": turnaround_violations,
            "longStreaks": long_streaks
        },
        "at_risk": at_risk_staff
    }


def find_turnaround_violations(shifts: List[Dict]) -> List[Dict]:
    """Find close-to-open turnarounds under threshold."""
    violations = []
    
    for i in range(len(shifts) - 1):
        current = shifts[i]
        next_shift = shifts[i + 1]
        
        # Parse end time of current and start time of next
        current_end = parse_shift_datetime(current["date"], current.get("end_time", "22:00"))
        next_start = parse_shift_datetime(next_shift["date"], next_shift.get("start_time", "10:00"))
        
        gap_hours = (next_start - current_end).total_seconds() / 3600
        
        if 0 < gap_hours < MIN_TURNAROUND_HOURS:
            violations.append({
                "staff_id": current["staff_id"],
                "date": next_shift["date"],
                "gap": f"{gap_hours:.0f} hours",
                "severity": "critical" if gap_hours < 8 else "warning"
            })
    
    return violations


def find_consecutive_closes(all_shifts: List[Dict], week_shifts: List[Dict]) -> int:
    """Find longest streak of consecutive closing shifts."""
    if not all_shifts:
        return 0
    
    # Sort by date
    sorted_shifts = sorted(all_shifts, key=lambda x: x["date"])
    
    max_streak = 0
    current_streak = 0
    prev_date = None
    
    for shift in sorted_shifts:
        if is_close_shift(shift):
            shift_date = datetime.strptime(shift["date"], "%Y-%m-%d").date()
            
            if prev_date is None or (shift_date - prev_date).days == 1:
                current_streak += 1
            else:
                current_streak = 1
            
            prev_date = shift_date
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
            prev_date = None
    
    return max_streak


def find_consecutive_days(shifts: List[Dict]) -> int:
    """Find longest streak of consecutive days worked."""
    if not shifts:
        return 0
    
    dates = sorted(set(datetime.strptime(s["date"], "%Y-%m-%d").date() for s in shifts))
    
    if not dates:
        return 0
    
    max_streak = 1
    current_streak = 1
    
    for i in range(1, len(dates)):
        if (dates[i] - dates[i-1]).days == 1:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 1
    
    return max_streak


# ═══════════════════════════════════════════════════════════════════════════
# PREFERENCE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════

def analyze_preferences(
    shifts: List[Dict],
    profile_lookup: Dict,
    staff_lookup: Dict
) -> Dict[str, Any]:
    """
    Analyze preference alignment using three-layer model.
    """
    conflicts = []
    score = 100
    
    # Group shifts by staff
    staff_shifts = defaultdict(list)
    for shift in shifts:
        staff_shifts[shift["staff_id"]].append(shift)
    
    for staff_id, week_shifts in staff_shifts.items():
        profile = profile_lookup.get(staff_id)
        if not profile:
            continue  # No profile = no constraints to check
        
        staff_name = staff_lookup.get(staff_id, {}).get("full_name", "Unknown")
        
        # LAYER 1: Availability violations (CRITICAL)
        unavailable_days = profile.get("unavailable_days") or []
        for shift in week_shifts:
            shift_date = datetime.strptime(shift["date"], "%Y-%m-%d")
            day_name = shift_date.strftime("%A")
            
            if day_name in unavailable_days:
                conflicts.append({
                    "staff": staff_name,
                    "staff_id": staff_id,
                    "type": "availability",
                    "severity": "critical",
                    "detail": f"Scheduled {day_name} — marked unavailable ({profile.get('availability_reason', 'no reason given')})",
                    "resolution": "Needs coverage or staff confirmation"
                })
                score -= 15
        
        # Check time constraints
        unavailable_before = profile.get("unavailable_before")
        unavailable_after = profile.get("unavailable_after")
        
        for shift in week_shifts:
            start_time = shift.get("start_time", "10:00")
            end_time = shift.get("end_time", "22:00")
            
            if unavailable_before:
                shift_start = datetime.strptime(start_time, "%H:%M").time()
                constraint_time = datetime.strptime(str(unavailable_before), "%H:%M:%S").time()
                if shift_start < constraint_time:
                    conflicts.append({
                        "staff": staff_name,
                        "staff_id": staff_id,
                        "type": "availability",
                        "severity": "critical",
                        "detail": f"Shift starts at {start_time}, unavailable before {constraint_time.strftime('%I:%M %p')}",
                        "resolution": "Adjust start time or reassign"
                    })
                    score -= 15
            
            if unavailable_after:
                shift_end = datetime.strptime(end_time, "%H:%M").time()
                constraint_time = datetime.strptime(str(unavailable_after), "%H:%M:%S").time()
                if shift_end > constraint_time:
                    conflicts.append({
                        "staff": staff_name,
                        "staff_id": staff_id,
                        "type": "availability",
                        "severity": "critical",
                        "detail": f"Shift ends at {end_time}, must be done by {constraint_time.strftime('%I:%M %p')}",
                        "resolution": "Adjust end time or reassign"
                    })
                    score -= 15
        
        # LAYER 2: Hours exceeded (MEDIUM)
        weekly_hours = sum(calculate_shift_hours(s) for s in week_shifts)
        preferred_max = profile.get("preferred_max_hours")
        
        if preferred_max and weekly_hours > preferred_max:
            overage = weekly_hours - preferred_max
            conflicts.append({
                "staff": staff_name,
                "staff_id": staff_id,
                "type": "hours",
                "severity": "medium",
                "detail": f"Scheduled {weekly_hours:.0f}hrs, requested max {preferred_max}hrs",
                "resolution": f"{overage:.0f}hr overage — confirm with staff"
            })
            score -= 8
        
        # LAYER 3: Preference drift (LOW - retention signal)
        hired_shift = profile.get("hired_shift")
        preferred_shift = profile.get("preferred_shift")
        preference_updated = profile.get("preference_updated_at")
        
        if preferred_shift and hired_shift and preferred_shift != hired_shift:
            # Check how long this drift has existed
            if preference_updated:
                if isinstance(preference_updated, str):
                    pref_date = datetime.fromisoformat(preference_updated.replace("Z", "+00:00"))
                else:
                    pref_date = preference_updated
                weeks_of_drift = (datetime.now(pref_date.tzinfo) - pref_date).days // 7
            else:
                weeks_of_drift = 0
            
            if weeks_of_drift >= 3:
                conflicts.append({
                    "staff": staff_name,
                    "staff_id": staff_id,
                    "type": "preference_drift",
                    "severity": "low",
                    "detail": f"Prefers {preferred_shift} shifts (noted {weeks_of_drift} weeks ago), hired for {hired_shift}",
                    "resolution": "Consider 1:1 conversation about role adjustment"
                })
                score -= 5
    
    # Cap score at 0
    score = max(score, 0)
    
    # Count by severity
    critical_count = len([c for c in conflicts if c["severity"] == "critical"])
    medium_count = len([c for c in conflicts if c["severity"] == "medium"])
    low_count = len([c for c in conflicts if c["severity"] == "low"])
    
    # Interpretation
    if score >= 90:
        interpretation = "Excellent alignment with staff preferences and availability."
    elif score >= 75:
        interpretation = f"Good alignment. {len(conflicts)} minor conflicts to review."
    elif score >= 60:
        interpretation = f"Several preference conflicts. {critical_count} critical issues need resolution."
    else:
        interpretation = f"Significant conflicts. {critical_count} availability violations will likely cause call-offs."
    
    return {
        "score": score,
        "interpretation": interpretation,
        "details": {
            "alignmentScore": score,
            "totalConflicts": len(conflicts),
            "criticalConflicts": critical_count,
            "conflicts": conflicts
        },
        "conflicts": conflicts
    }


# ═══════════════════════════════════════════════════════════════════════════
# PRIORITY FIXES GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

def generate_priority_fixes(
    fairness_result: Dict,
    fatigue_result: Dict,
    preference_result: Dict,
    staff_lookup: Dict
) -> List[Dict]:
    """
    Generate prioritized list of fixes from all analyses.
    """
    fixes = []
    fix_id = 1
    
    # Critical availability violations first
    for conflict in preference_result.get("conflicts", []):
        if conflict["severity"] == "critical":
            fixes.append({
                "id": f"FIX-{fix_id:03d}",
                "priority": fix_id,
                "type": "preference",
                "severity": "high",
                "title": f"{conflict['staff']} — Availability conflict",
                "description": conflict["detail"],
                "affectedStaff": [conflict["staff"]],
                "suggestedAction": conflict["resolution"],
                "impact": "Removes critical violation, prevents likely call-off",
                "autoFixAvailable": False
            })
            fix_id += 1
    
    # High fatigue risks
    for at_risk in fatigue_result.get("at_risk", []):
        if at_risk["burnout_probability"] >= 0.6:
            staff_name = staff_lookup.get(at_risk["staff_id"], {}).get("full_name", "Unknown")
            factors = ", ".join(at_risk["factors"][:2])
            
            fixes.append({
                "id": f"FIX-{fix_id:03d}",
                "priority": fix_id,
                "type": "fatigue",
                "severity": "high",
                "title": f"{staff_name} — Burnout risk {int(at_risk['burnout_probability']*100)}%",
                "description": f"Factors: {factors}",
                "affectedStaff": [staff_name],
                "suggestedAction": "Reduce closing load or swap one shift",
                "impact": f"-{int(at_risk['burnout_probability']*30)}% burnout risk",
                "autoFixAvailable": "closes" in factors.lower()
            })
            fix_id += 1
    
    # Fairness imbalances
    for imbalance in fairness_result.get("issues", []):
        if imbalance["severity"] in ["high", "medium"]:
            fixes.append({
                "id": f"FIX-{fix_id:03d}",
                "priority": fix_id,
                "type": "fairness",
                "severity": imbalance["severity"],
                "title": f"Fairness — {imbalance['metric']}",
                "description": imbalance["issue"],
                "affectedStaff": [],
                "suggestedAction": "Redistribute shifts more evenly",
                "impact": "+5-10 fairness score",
                "autoFixAvailable": False
            })
            fix_id += 1
    
    # Hours exceeded
    for conflict in preference_result.get("conflicts", []):
        if conflict["type"] == "hours":
            fixes.append({
                "id": f"FIX-{fix_id:03d}",
                "priority": fix_id,
                "type": "preference",
                "severity": "medium",
                "title": f"{conflict['staff']} — Hours exceeded",
                "description": conflict["detail"],
                "affectedStaff": [conflict["staff"]],
                "suggestedAction": conflict["resolution"],
                "impact": "Respects staff boundary, improves retention",
                "autoFixAvailable": False
            })
            fix_id += 1
    
    # Preference drift (lower priority)
    for conflict in preference_result.get("conflicts", []):
        if conflict["type"] == "preference_drift":
            fixes.append({
                "id": f"FIX-{fix_id:03d}",
                "priority": fix_id,
                "type": "preference",
                "severity": "low",
                "title": f"{conflict['staff']} — Preference drift",
                "description": conflict["detail"],
                "affectedStaff": [conflict["staff"]],
                "suggestedAction": conflict["resolution"],
                "impact": "Retention risk signal — schedule 1:1",
                "autoFixAvailable": False
            })
            fix_id += 1
    
    return fixes[:10]  # Cap at 10 most important


# ═══════════════════════════════════════════════════════════════════════════
# STAFF IMPACT SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

def generate_staff_impact(
    shifts: List[Dict],
    fairness_result: Dict,
    fatigue_result: Dict,
    preference_result: Dict,
    profile_lookup: Dict,
    staff_lookup: Dict
) -> List[Dict]:
    """
    Generate per-staff impact summary.
    """
    # Collect all issues by staff
    staff_issues = defaultdict(list)
    
    for conflict in preference_result.get("conflicts", []):
        staff_issues[conflict["staff_id"]].append(conflict)
    
    for at_risk in fatigue_result.get("at_risk", []):
        staff_issues[at_risk["staff_id"]].append({
            "type": "fatigue",
            "severity": "high" if at_risk["burnout_probability"] >= 0.6 else "medium",
            "detail": f"Burnout risk: {int(at_risk['burnout_probability']*100)}%"
        })
    
    # Build impact summary for affected staff
    impact_list = []
    
    for staff_id, issues in staff_issues.items():
        if not issues:
            continue
        
        staff_name = staff_lookup.get(staff_id, {}).get("full_name", "Unknown")
        position = staff_lookup.get(staff_id, {}).get("position", "Staff")
        
        # Calculate risk level
        has_critical = any(i.get("severity") == "critical" for i in issues)
        has_high = any(i.get("severity") == "high" for i in issues)
        
        if has_critical:
            risk_level = "high"
        elif has_high:
            risk_level = "medium"
        else:
            risk_level = "low"
        
        # Main issue
        if has_critical:
            main_issue = "Availability conflict"
        elif has_high:
            main_issue = "Burnout risk"
        else:
            main_issue = "Preference concern"
        
        impact_list.append({
            "id": staff_id,
            "name": staff_name,
            "role": position,
            "riskLevel": risk_level,
            "issueCount": len(issues),
            "mainIssue": main_issue,
            "details": issues[0].get("detail", "See flags for details")
        })
    
    # Sort by risk level
    risk_order = {"high": 0, "medium": 1, "low": 2}
    impact_list.sort(key=lambda x: risk_order.get(x["riskLevel"], 3))
    
    return impact_list[:10]


# ═══════════════════════════════════════════════════════════════════════════
# EMOTIONAL FALLOUT PREDICTIONS
# ═══════════════════════════════════════════════════════════════════════════

def predict_emotional_fallout(
    shifts: List[Dict],
    preference_result: Dict,
    fatigue_result: Dict,
    profile_lookup: Dict,
    staff_lookup: Dict
) -> Dict[str, Any]:
    """
    Predict resentment, call-offs, and tension spots.
    """
    resentment = []
    call_off_risk = []
    tension_spots = []
    
    # Resentment from preference drift
    for conflict in preference_result.get("conflicts", []):
        if conflict["type"] == "preference_drift":
            resentment.append({
                "staff": conflict["staff"],
                "probability": 0.65,
                "trigger": conflict["detail"],
                "likelyBehavior": "Disengagement, reduced effort",
                "preventionAction": "Acknowledge in 1:1, discuss role adjustment"
            })
    
    # Resentment from fatigue
    for at_risk in fatigue_result.get("at_risk", []):
        if at_risk["burnout_probability"] >= 0.7:
            staff_name = staff_lookup.get(at_risk["staff_id"], {}).get("full_name", "Unknown")
            resentment.append({
                "staff": staff_name,
                "probability": at_risk["burnout_probability"] * 0.8,
                "trigger": f"Burnout from {', '.join(at_risk['factors'][:2])}",
                "likelyBehavior": "Call-off risk, visible frustration",
                "preventionAction": "Reduce load, acknowledge publicly"
            })
    
    # Call-off risks
    for conflict in preference_result.get("conflicts", []):
        if conflict["severity"] == "critical":
            call_off_risk.append({
                "staff": conflict["staff"],
                "day": "Scheduled unavailable day",
                "probability": 0.45,
                "reason": conflict["detail"]
            })
    
    for at_risk in fatigue_result.get("at_risk", []):
        if at_risk["burnout_probability"] >= 0.6:
            staff_name = staff_lookup.get(at_risk["staff_id"], {}).get("full_name", "Unknown")
            call_off_risk.append({
                "staff": staff_name,
                "day": "Late in week",
                "probability": at_risk["burnout_probability"] * 0.35,
                "reason": "Accumulated fatigue"
            })
    
    # Tension spots (understaffing scenarios)
    if len(call_off_risk) > 0:
        tension_spots.append({
            "context": "Weekend service",
            "issue": f"{len(call_off_risk)} staff at risk of calling off",
            "severity": "medium" if len(call_off_risk) <= 2 else "high"
        })
    
    return {
        "predictedResentment": resentment[:5],
        "callOffRisk": call_off_risk[:5],
        "tensionSpots": tension_spots[:3]
    }


# ═══════════════════════════════════════════════════════════════════════════
# SSE EVENT GENERATION
# ═══════════════════════════════════════════════════════════════════════════

def generate_sse_events(
    fatigue_result: Dict,
    preference_result: Dict,
    staff_lookup: Dict
) -> List[Dict]:
    """
    Generate SSE escalation events to auto-create.
    """
    events = []
    
    # Burnout risk events
    for at_risk in fatigue_result.get("at_risk", []):
        if at_risk["burnout_probability"] >= 0.7:
            staff_name = staff_lookup.get(at_risk["staff_id"], {}).get("full_name", "Unknown")
            events.append({
                "id": f"SSE-AUTO-{len(events)+1}",
                "type": "burnout",
                "severity": "high",
                "staff": staff_name,
                "staff_id": at_risk["staff_id"],
                "trigger": f"Schedule analysis: {int(at_risk['burnout_probability']*100)}% burnout risk",
                "status": "pending_review",
                "autoCreated": True
            })
    
    # Preference drift events
    for conflict in preference_result.get("conflicts", []):
        if conflict["type"] == "preference_drift":
            events.append({
                "id": f"SSE-AUTO-{len(events)+1}",
                "type": "fairness",
                "severity": "medium",
                "staff": conflict["staff"],
                "staff_id": conflict["staff_id"],
                "trigger": f"Schedule analysis: {conflict['detail']}",
                "status": "pending_review",
                "autoCreated": True
            })
    
    return events


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_work_profiles(organization_id: int) -> List[Dict]:
    """Fetch all work profiles for restaurant."""
    try:
        result = supabase.table("staff_work_profile") \
            .select("*") \
            .eq("organization_id", organization_id) \
            .execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error fetching work profiles: {e}")
        return []


def get_staff_lookup(organization_id: int) -> Dict[str, Dict]:
    """Get staff lookup dictionary."""
    try:
        result = supabase.table("staff") \
            .select("staff_id, full_name, position") \
            .eq("organization_id", organization_id) \
            .eq("status", "Active") \
            .execute()
        return {s["staff_id"]: s for s in (result.data or [])}
    except Exception as e:
        logger.error(f"Error fetching staff: {e}")
        return {}


def get_historical_shifts(organization_id: int, week_of: str, weeks_back: int = 4) -> List[Dict]:
    """Fetch historical shifts for trend analysis."""
    try:
        end_date = datetime.strptime(week_of, "%Y-%m-%d").date()
        start_date = end_date - timedelta(weeks=weeks_back)
        
        result = supabase.table("sse_shifts") \
            .select("staff_id, shift_date, scheduled_start, scheduled_end, shift_type, position") \
            .eq("organization_id", organization_id) \
            .gte("shift_date", start_date.isoformat()) \
            .lt("shift_date", week_of) \
            .execute()
        
        # Normalize to match parsed format
        shifts = []
        for s in (result.data or []):
            shifts.append({
                "staff_id": s["staff_id"],
                "date": s["shift_date"],
                "start_time": extract_time(s.get("scheduled_start")),
                "end_time": extract_time(s.get("scheduled_end")),
                "position": s.get("position") or s.get("shift_type")
            })
        return shifts
    except Exception as e:
        logger.error(f"Error fetching historical shifts: {e}")
        return []


def extract_time(timestamp: Optional[str]) -> str:
    """Extract HH:MM from timestamp."""
    if not timestamp:
        return "12:00"
    try:
        if "T" in timestamp:
            return timestamp.split("T")[1][:5]
        return timestamp[:5]
    except:
        return "12:00"


def calculate_shift_hours(shift: Dict) -> float:
    """Calculate hours for a shift."""
    try:
        start = datetime.strptime(shift.get("start_time", "10:00"), "%H:%M")
        end = datetime.strptime(shift.get("end_time", "18:00"), "%H:%M")
        
        # Handle overnight
        if end < start:
            end = end + timedelta(days=1)
        
        return (end - start).seconds / 3600
    except:
        return 6  # Default assumption


def is_am_shift(shift: Dict) -> bool:
    """Check if shift is an AM/opening shift."""
    start = shift.get("start_time", "12:00")
    try:
        hour = int(start.split(":")[0])
        return hour < 12
    except:
        return False


def is_pm_shift(shift: Dict) -> bool:
    """Check if shift is a PM shift (not close)."""
    start = shift.get("start_time", "12:00")
    end = shift.get("end_time", "22:00")
    try:
        start_hour = int(start.split(":")[0])
        end_hour = int(end.split(":")[0])
        return start_hour >= 12 and end_hour <= 22
    except:
        return False


def is_close_shift(shift: Dict) -> bool:
    """Check if shift is a closing shift."""
    end = shift.get("end_time", "22:00")
    try:
        hour = int(end.split(":")[0])
        return hour >= 22 or hour <= 2  # Ends at 10pm+ or past midnight
    except:
        return False


def parse_shift_datetime(date_str: str, time_str: str) -> datetime:
    """Parse date and time into datetime."""
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")


def get_status(score: int) -> str:
    """Get status string from score."""
    if score >= 80:
        return "ok"
    elif score >= 60:
        return "warning"
    return "critical"


def get_stability_interpretation(score: int, fix_count: int) -> str:
    """Get stability interpretation."""
    if score >= 85:
        return "Schedule looks solid. Minor optimizations optional."
    elif score >= 75:
        return f"Good schedule. {fix_count} items to review before publishing."
    elif score >= 65:
        return f"Moderate risk. {fix_count} issues need attention before publish."
    else:
        return f"High risk schedule. Address {fix_count} critical issues before publishing."


def calculate_predicted_reaction(fairness: int, fatigue: int, preference: int, fix_count: int) -> Dict:
    """Calculate predicted staff reaction."""
    # Simple model based on scores
    avg_score = (fairness + (100 - fatigue) + preference) / 3
    
    if avg_score >= 80:
        positive, neutral, negative = 0.65, 0.25, 0.10
        summary = "Likely positive reception. Schedule respects staff needs."
    elif avg_score >= 70:
        positive, neutral, negative = 0.45, 0.35, 0.20
        summary = f"Mixed reception likely. Address {fix_count} issues to improve."
    elif avg_score >= 60:
        positive, neutral, negative = 0.25, 0.35, 0.40
        summary = "Negative reactions expected. Multiple staff will feel overlooked."
    else:
        positive, neutral, negative = 0.15, 0.25, 0.60
        summary = "High likelihood of complaints and call-offs. Immediate fixes needed."
    
    return {
        "positive": positive,
        "neutral": neutral,
        "negative": negative,
        "summary": summary
    }


def calculate_smm_prediction(stability_score: int) -> Dict:
    """Predict SMM score movement."""
    # Simple model
    if stability_score >= 85:
        value, low, high = 1.5, 0.5, 2.5
    elif stability_score >= 75:
        value, low, high = 0.2, -0.5, 1.0
    elif stability_score >= 65:
        value, low, high = -1.2, -2.5, 0.3
    else:
        value, low, high = -3.0, -5.0, -1.0
    
    return {
        "value": value,
        "range": [low, high],
        "confidence": 0.72
    }