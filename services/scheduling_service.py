from database.supabase_client import get_supabase
from typing import List, Dict, Optional
from datetime import datetime, date, time, timedelta
import uuid

class SchedulingService:
    def __init__(self):
        self.supabase = get_supabase()
    
    async def optimize_schedule(
        self, 
        restaurant_id: int, 
        pay_period_start: str, 
        pay_period_end: str,
        created_by: str
    ) -> Dict:
        """
        Generate optimized schedule using greedy algorithm
        
        Returns:
            {
                schedule_id: UUID,
                coverage_percent: float,
                efficiency_percent: float,
                estimated_cost: float,
                total_hours: float,
                constraint_violations: int,
                has_coverage_gaps: bool
            }
        """
        
        # 1. Load all data
        staff = await self._load_staff(restaurant_id)
        constraints = await self._load_constraints(restaurant_id)
        demand = await self._load_demand_curve(restaurant_id)
        
        # 2. Initialize tracking
        staff_hours = {s['staff_id']: 0 for s in staff}
        all_shifts = []
        total_demand_slots = 0
        filled_slots = 0
        violations = 0
        total_cost = 0.0
        
        # 3. Generate date range
        start_date = datetime.fromisoformat(pay_period_start).date()
        end_date = datetime.fromisoformat(pay_period_end).date()
        days = (end_date - start_date).days + 1
        
        # 4. Main scheduling loop
        for day_offset in range(days):
            current_date = start_date + timedelta(days=day_offset)
            day_type = 'weekend' if current_date.weekday() >= 5 else 'weekday'
            
            # Schedule each hour of operation (9 AM to 11 PM)
            for hour in range(9, 24):
                # Get demand for this hour
                staff_needed = self._get_demand(demand, day_type, hour)
                total_demand_slots += staff_needed
                
                # Filter available staff for this time slot
                available = self._filter_available_staff(
                    staff, 
                    constraints, 
                    current_date, 
                    hour,
                    staff_hours
                )
                
                # Sort by cost-effectiveness
                available.sort(key=lambda s: self._calculate_cost_effectiveness(s))
                
                # Assign staff to this shift
                assigned_count = min(len(available), staff_needed)
                
                for i in range(assigned_count):
                    staff_member = available[i]
                    shift = self._create_shift(
                        staff_member,
                        current_date,
                        hour
                    )
                    all_shifts.append(shift)
                    
                    # Update tracking
                    staff_hours[staff_member['staff_id']] += 1
                    total_cost += float(staff_member['hourly_rate'])
                    filled_slots += 1
                
                # Track violations (unfilled demand)
                if assigned_count < staff_needed:
                    violations += (staff_needed - assigned_count)
        
        # 5. Calculate metrics
        coverage_percent = (filled_slots / total_demand_slots * 100) if total_demand_slots > 0 else 0
        efficiency_percent = self._calculate_efficiency(all_shifts, demand)
        total_hours = sum(staff_hours.values())
        has_coverage_gaps = violations > 0
        
        # 6. Save to database
        schedule_id = await self._save_schedule(
            restaurant_id=restaurant_id,
            shifts=all_shifts,
            coverage_score=coverage_percent,
            total_cost=total_cost,
            total_hours=total_hours,
            violations=violations,
            created_by=created_by
        )
        
        return {
            "schedule_id": schedule_id,
            "coverage_percent": round(coverage_percent, 1),
            "efficiency_percent": round(efficiency_percent, 1),
            "estimated_cost": round(total_cost, 2),
            "total_hours": round(total_hours, 1),
            "constraint_violations": violations,
            "has_coverage_gaps": has_coverage_gaps
        }
    
    async def _load_staff(self, restaurant_id: int) -> List[Dict]:
        """Load all active staff with capacity metrics"""
        response = self.supabase.from_('staff') \
            .select('staff_id, full_name, position, hourly_rate, max_hours_per_week, capacity_rating, productivity_multiplier') \
            .eq('restaurant_id', restaurant_id) \
            .eq('status', 'Active') \
            .execute()
        
        return response.data
    
    async def _load_constraints(self, restaurant_id: int) -> List[Dict]:
        """Load all active scheduling constraints"""
        response = self.supabase.from_('staff_scheduling_rules') \
            .select('*') \
            .eq('restaurant_id', restaurant_id) \
            .eq('is_active', True) \
            .execute()
        
        return response.data
    
    async def _load_demand_curve(self, restaurant_id: int) -> Dict:
        """Load demand curve - for now returns mock data until table exists"""
        # TODO: Once demand_curves table exists, load from DB
        # For now, return realistic mock data
        weekday_demand = {
            9: 2, 10: 2, 11: 3, 12: 7, 13: 7, 14: 5,
            15: 3, 16: 3, 17: 4, 18: 9, 19: 9, 20: 8,
            21: 6, 22: 3, 23: 2
        }
        weekend_demand = {
            9: 3, 10: 4, 11: 5, 12: 8, 13: 8, 14: 6,
            15: 4, 16: 4, 17: 6, 18: 10, 19: 10, 20: 9,
            21: 7, 22: 4, 23: 2
        }
        return {
            'weekday': weekday_demand,
            'weekend': weekend_demand
        }
    
    def _get_demand(self, demand: Dict, day_type: str, hour: int) -> int:
        """Get staff needed for specific day type and hour"""
        return demand.get(day_type, {}).get(hour, 2)
    
    def _filter_available_staff(
        self, 
        staff: List[Dict], 
        constraints: List[Dict], 
        current_date: date,
        hour: int,
        staff_hours: Dict[str, int]
    ) -> List[Dict]:
        """Filter staff who can work this slot based on constraints"""
        available = []
        
        for staff_member in staff:
            staff_id = staff_member['staff_id']
            
            # Check if at max hours for week
            if staff_hours[staff_id] >= staff_member['max_hours_per_week']:
                continue
            
            # Check constraints
            if self._violates_constraints(staff_id, constraints, current_date, hour):
                continue
            
            available.append(staff_member)
        
        return available
    
    def _violates_constraints(
        self, 
        staff_id: str, 
        constraints: List[Dict], 
        current_date: date,
        hour: int
    ) -> bool:
        """Check if scheduling this staff member violates any constraints"""
        staff_constraints = [c for c in constraints if c['staff_id'] == staff_id]
        
        for constraint in staff_constraints:
            # Check PTO constraints
            if constraint['rule_type'] == 'pto':
                pto_start = datetime.fromisoformat(constraint['pto_start_date']).date()
                pto_end = datetime.fromisoformat(constraint['pto_end_date']).date()
                if pto_start <= current_date <= pto_end:
                    return True
            
            # Check recurring constraints
            elif constraint['rule_type'] == 'recurring':
                recurrence_type = constraint.get('recurrence_type', '')
                
                # Check end date if exists
                if constraint.get('recurrence_end_date'):
                    end_date = datetime.fromisoformat(constraint['recurrence_end_date']).date()
                    if current_date > end_date:
                        continue  # Constraint expired
                
                # Check specific constraint types
                if recurrence_type == 'cannot_work_specific_days':
                    blocked_days = constraint.get('blocked_days', [])
                    current_day_of_week = current_date.weekday()  # Monday=0, Tuesday=1, ..., Sunday=6
                    if blocked_days and current_day_of_week in blocked_days:
                        return True
                
                elif recurrence_type == 'cannot_work_before_time':
                    if hour < 12:  # Simplified: can't work mornings
                        return True
                
                elif recurrence_type == 'cannot_work_after_time':
                    if hour >= 22:  # Simplified: can't work late
                        return True
                
                elif recurrence_type == 'no_weekends':
                    if current_date.weekday() >= 5:
                        return True
                
                elif recurrence_type == 'weekends_only':
                    if current_date.weekday() < 5:
                        return True
                
                elif recurrence_type == 'no_opening_shifts':
                    if hour < 10:
                        return True
                
                elif recurrence_type == 'no_closing_shifts':
                    if hour >= 22:
                        return True
        
        return False
    
    def _calculate_cost_effectiveness(self, staff_member: Dict) -> float:
        """Calculate cost per unit of capacity (lower is better)"""
        capacity_rating = staff_member.get('capacity_rating', 3)
        productivity = staff_member.get('productivity_multiplier', 1.0)
        hourly_rate = float(staff_member['hourly_rate'])
        
        effective_capacity = capacity_rating * productivity
        
        # Avoid division by zero
        if effective_capacity == 0:
            return 999999.0
        
        return hourly_rate / effective_capacity
    
    def _create_shift(self, staff_member: Dict, shift_date: date, hour: int) -> Dict:
        """Create a shift record"""
        return {
            'staff_id': staff_member['staff_id'],
            'date': shift_date.isoformat(),
            'start_time': f"{hour:02d}:00:00",
            'end_time': f"{hour+1:02d}:00:00",
            'position': staff_member['position'],
            'hourly_rate': float(staff_member['hourly_rate']),
            'capacity_rating': staff_member.get('capacity_rating', 3),
            'productivity_multiplier': float(staff_member.get('productivity_multiplier', 1.0))
        }
    
    def _calculate_efficiency(self, shifts: List[Dict], demand: Dict) -> float:
        """Calculate efficiency percentage"""
        if not shifts:
            return 0.0
        
        # Calculate total hours scheduled
        hours_scheduled = len(shifts)

        # Calculate total hours needed from demand curve
        hours_needed = 0
        for day_type in ['weekday', 'weekend']:
            if day_type in demand:
                for hour, staff_count in demand[day_type].items():
                    # Weekdays: 10 days (Mon-Fri × 2 weeks)
                    # Weekends: 4 days (Sat-Sun × 2 weeks)
                    days = 10 if day_type == 'weekday' else 4
                    hours_needed += staff_count * days
        
        if hours_needed == 0:
            return 0.0
        
        # Efficiency = actual hours / needed hours × 100
        # 100% = perfectly matched, >100% = overstaffed, <100% = understaffed
        efficiency = (hours_scheduled / hours_needed) * 100
        
        return round(efficiency, 1)
    
    async def _save_schedule(
        self,
        restaurant_id: int,
        shifts: List[Dict],
        coverage_score: float,
        total_cost: float,
        total_hours: float,
        violations: int,
        created_by: str
    ) -> str:
        """Save schedule to database"""
        
        # Create parent schedule record
        schedule_data = {
            'restaurant_id': restaurant_id,
            'scenario_name': f'AI Schedule {datetime.now().strftime("%Y-%m-%d %H:%M")}',
            'total_labor_cost': total_cost,
            'total_labor_hours': total_hours,
            'coverage_score': coverage_score,
            'constraint_violations': violations,
            'schedule_data': {'created_by': created_by},
            'is_selected': True
        }
        
        schedule_response = self.supabase.from_('generated_schedules') \
            .insert(schedule_data) \
            .execute()
        
        if not schedule_response.data:
            raise Exception("Failed to create schedule")
        
        schedule_id = schedule_response.data[0]['id']
        
        # Create shift records
        shift_records = []
        for shift in shifts:
            shift_record = {
                'generated_schedule_id': schedule_id,
                'restaurant_id': restaurant_id,
                'staff_id': shift['staff_id'],
                'date': shift['date'],
                'start_time': shift['start_time'],
                'end_time': shift['end_time'],
                'position': shift['position'],
                'confidence_score': shift['capacity_rating'] * shift['productivity_multiplier'] / 5.0,
                'constraint_flags': {}
            }
            shift_records.append(shift_record)
        
        # Batch insert shifts
        if shift_records:
            self.supabase.from_('generated_shifts') \
                .insert(shift_records) \
                .execute()
        
        return schedule_id
    
async def approve_and_post_gaps(self, schedule_id: str, approved_by: str) -> Dict:
    """Approve schedule and bulk create open shifts for gaps"""
    
    # 1. Get the schedule
    schedule = await self._get_schedule(schedule_id)
    
    # 2. Find all gaps (compare demand vs assigned shifts)
    gaps = await self._find_schedule_gaps(schedule_id, schedule['restaurant_id'])
    
    # 3. Mark schedule as approved
    self.supabase.from_('generated_schedules') \
        .update({'status': 'approved', 'approved_by': approved_by, 'approved_at': datetime.now()}) \
        .eq('id', schedule_id) \
        .execute()
    
    # 4. Bulk create open shifts
    if gaps:
        open_shifts = [self._gap_to_open_shift(gap, schedule['restaurant_id']) for gap in gaps]
        self.supabase.from_('open_shifts').insert(open_shifts).execute()
    
    return {
        'scheduled_count': await self._count_scheduled_shifts(schedule_id),
        'open_shifts_count': len(gaps)
    }