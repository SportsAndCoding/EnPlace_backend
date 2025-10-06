from fastapi import APIRouter, Depends, HTTPException
from services.auth_service import verify_jwt_token as get_current_user
from database.supabase_client import get_supabase
from datetime import datetime, timedelta
from typing import Dict, List

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

@router.get("/{schedule_id}/review")
async def get_schedule_review(
    schedule_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get organized schedule data for review
    
    Returns:
    - Schedule metadata
    - Shifts organized by day/hour/position
    - Coverage gaps per day/hour/position
    - Suggested open shifts to post
    """
    supabase = get_supabase()
    
    # 1. Load schedule metadata
    schedule_response = supabase.from_('generated_schedules') \
        .select('*') \
        .eq('id', schedule_id) \
        .single() \
        .execute()
    
    if not schedule_response.data:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    schedule = schedule_response.data
    restaurant_id = schedule['restaurant_id']
    
    # Verify access
    if current_user['restaurant_id'] != restaurant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # 2. Load all shifts for this schedule
    shifts_response = supabase.from_('generated_shifts') \
        .select('*, staff:staff_id(full_name, position)') \
        .eq('generated_schedule_id', schedule_id) \
        .execute()
    
    shifts = shifts_response.data or []
    
    # 3. Load demand patterns to calculate gaps
    # Need to figure out the pay period dates from the schedule
    # For now, we'll calculate from the shifts themselves
    if shifts:
        dates = sorted(set(s['date'] for s in shifts))
        pay_period_start = dates[0]
        pay_period_end = dates[-1]
    else:
        # No shifts, can't determine period
        pay_period_start = None
        pay_period_end = None
    
    # 4. Load restaurant settings for role ratios
    restaurant_response = supabase.from_('restaurants') \
        .select('role_ratios') \
        .eq('id', restaurant_id) \
        .single() \
        .execute()
    
    role_ratios = restaurant_response.data.get('role_ratios', {}) if restaurant_response.data else {}
    
    # 5. Load demand curve
    demand_response = supabase.from_('restaurant_demand_patterns') \
        .select('day_type, hour, covers_per_hour') \
        .eq('restaurant_id', restaurant_id) \
        .execute()
    
    demand_data = {}
    for row in demand_response.data or []:
        if row['day_type'] not in demand_data:
            demand_data[row['day_type']] = {}
        demand_data[row['day_type']][row['hour']] = row['covers_per_hour']
    
    # 6. Organize shifts into grid structure
    organized = organize_shifts_by_day(shifts, pay_period_start, pay_period_end)
    
    # 7. Calculate coverage gaps
    gaps = calculate_coverage_gaps(organized, demand_data, role_ratios, pay_period_start, pay_period_end)
    
    # 8. Generate suggested open shifts
    suggested_open_shifts = generate_open_shift_suggestions(gaps)
    
    return {
        "schedule_id": schedule_id,
        "scenario_name": schedule['scenario_name'],
        "pay_period_start": pay_period_start,
        "pay_period_end": pay_period_end,
        "total_cost": schedule['total_labor_cost'],
        "coverage_score": schedule['coverage_score'],
        "organized_shifts": organized,
        "coverage_gaps": gaps,
        "suggested_open_shifts": suggested_open_shifts,
        "gap_summary": {
            "total_gaps": sum(len(day_gaps) for day_gaps in gaps.values()),
            "critical_gaps": count_critical_gaps(gaps)
        }
    }

def organize_shifts_by_day(shifts: List[Dict], start_date: str, end_date: str) -> Dict:
    """
    Organize shifts into: {
        "2025-10-07": {
            "Server": {
                12: [{"staff_id": "...", "full_name": "..."}],
                13: [...]
            },
            "Cook": {...}
        }
    }
    """
    organized = {}
    
    for shift in shifts:
        date = shift['date']
        position = shift['position']
        hour = int(shift['start_time'].split(':')[0])
        
        if date not in organized:
            organized[date] = {}
        
        if position not in organized[date]:
            organized[date][position] = {}
        
        if hour not in organized[date][position]:
            organized[date][position][hour] = []
        
        organized[date][position][hour].append({
            "shift_id": shift['id'],
            "staff_id": shift['staff_id'],
            "full_name": shift['staff']['full_name'],
            "start_time": shift['start_time'],
            "end_time": shift['end_time']
        })
    
    return organized

def calculate_coverage_gaps(
    organized: Dict,
    demand_data: Dict,
    role_ratios: Dict,
    start_date: str,
    end_date: str
) -> Dict:
    """
    Calculate gaps: demand - actual for each day/hour/position
    
    Returns: {
        "2025-10-07": [
            {"hour": 12, "position": "Server", "needed": 8, "scheduled": 5, "gap": 3},
            ...
        ]
    }
    """
    gaps = {}
    
    if not start_date or not end_date:
        return gaps
    
    start = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()
    days = (end - start).days + 1
    
    for day_offset in range(days):
        current_date = start + timedelta(days=day_offset)
        date_str = current_date.isoformat()
        day_type = 'weekend' if current_date.weekday() >= 5 else 'weekday'
        
        gaps[date_str] = []
        
        for hour in range(9, 24):
            # Get covers demand
            covers = demand_data.get(day_type, {}).get(hour, 0)
            if covers == 0:
                continue
            
            # Convert to staff by role
            total_staff = max(1, round(covers / 4))
            
            for role, ratio in role_ratios.items():
                needed = max(1, round(total_staff * ratio))
                scheduled = len(organized.get(date_str, {}).get(role, {}).get(hour, []))
                gap = needed - scheduled
                
                if gap > 0:
                    gaps[date_str].append({
                        "hour": hour,
                        "position": role,
                        "needed": needed,
                        "scheduled": scheduled,
                        "gap": gap
                    })
    
    return gaps

def generate_open_shift_suggestions(gaps: Dict) -> List[Dict]:
    """
    Bundle gaps into logical open shift postings
    
    Strategy: Group consecutive hours for same position into single shifts
    """
    suggestions = []
    
    for date, day_gaps in gaps.items():
        # Group by position
        by_position = {}
        for gap in day_gaps:
            pos = gap['position']
            if pos not in by_position:
                by_position[pos] = []
            by_position[pos].append(gap)
        
        # Create shift suggestions
        for position, position_gaps in by_position.items():
            # Sort by hour
            position_gaps.sort(key=lambda x: x['hour'])
            
            # Bundle consecutive hours
            current_shift = None
            for gap in position_gaps:
                if current_shift is None:
                    current_shift = {
                        "date": date,
                        "position": position,
                        "start_hour": gap['hour'],
                        "end_hour": gap['hour'] + 1,
                        "slots_needed": gap['gap']
                    }
                elif gap['hour'] == current_shift['end_hour']:
                    # Consecutive hour, extend shift
                    current_shift['end_hour'] = gap['hour'] + 1
                    current_shift['slots_needed'] += gap['gap']
                else:
                    # Non-consecutive, save current and start new
                    suggestions.append(current_shift)
                    current_shift = {
                        "date": date,
                        "position": position,
                        "start_hour": gap['hour'],
                        "end_hour": gap['hour'] + 1,
                        "slots_needed": gap['gap']
                    }
            
            if current_shift:
                suggestions.append(current_shift)
    
    return suggestions

def count_critical_gaps(gaps: Dict) -> int:
    """Count gaps during peak hours (12-2pm, 6-9pm)"""
    critical = 0
    peak_hours = [12, 13, 18, 19, 20]
    
    for day_gaps in gaps.values():
        for gap in day_gaps:
            if gap['hour'] in peak_hours:
                critical += gap['gap']
    
    return critical