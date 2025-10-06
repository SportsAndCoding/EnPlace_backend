from typing import List, Dict
from datetime import datetime, date, timedelta

class ScheduleOptimizer:
    """
    Greedy scheduling algorithm that converts covers → staff by role
    
    Algorithm:
    1. Convert covers/hour to staff needed by role using role ratios
    2. For each day/hour/role, find available staff
    3. Sort by cost-effectiveness (hourly_rate / efficiency_multiplier)
    4. Assign best available staff up to demand
    5. Track violations (unfilled slots)
    """
    
    def __init__(
        self,
        restaurant_settings: Dict,
        staff: List[Dict],
        constraints: List[Dict],
        covers_demand: Dict,
        pay_period_start: str,
        pay_period_end: str
    ):
        self.restaurant = restaurant_settings
        self.staff = staff
        self.constraints = constraints
        self.covers_demand = covers_demand
        self.pay_period_start = datetime.fromisoformat(pay_period_start).date()
        self.pay_period_end = datetime.fromisoformat(pay_period_end).date()
        
        # Derived data
        self.role_ratios = restaurant_settings.get('role_ratios', self._default_ratios())
        self.staff_by_position = self._group_by_position(staff)
        self.staff_hours = {s['staff_id']: 0 for s in staff}
        
        # Tracking
        self.all_shifts = []
        self.total_demand_slots = 0
        self.filled_slots = 0
        self.violations = 0
        self.total_cost = 0.0
    
    def run(self) -> Dict:
        """Execute the optimization algorithm"""
        
        # Convert covers to staff demand by role
        staff_demand = self._convert_covers_to_staff_by_role()
        
        # Schedule each day/hour/role
        days = (self.pay_period_end - self.pay_period_start).days + 1
        
        for day_offset in range(days):
            current_date = self.pay_period_start + timedelta(days=day_offset)
            day_type = 'weekend' if current_date.weekday() >= 5 else 'weekday'
            
            for hour in range(9, 24):
                role_demand = staff_demand.get(day_type, {}).get(hour, {})
                
                for role, count_needed in role_demand.items():
                    self._schedule_role_for_hour(role, count_needed, current_date, hour)
        
        # Calculate final metrics
        return self._build_result()
    
    def _schedule_role_for_hour(self, role: str, count_needed: int, current_date: date, hour: int):
        """Schedule specific role for specific hour"""
        self.total_demand_slots += count_needed
        
        # Get staff for this role
        role_staff = self.staff_by_position.get(role, [])
        
        # Filter available
        available = [
            s for s in role_staff
            if self._is_available(s, current_date, hour)
        ]
        
        # Sort by cost-effectiveness (lower is better)
        available.sort(key=lambda s: self._cost_effectiveness(s))
        
        # Assign
        assigned_count = min(len(available), count_needed)
        
        for i in range(assigned_count):
            staff_member = available[i]
            self._assign_shift(staff_member, current_date, hour)
            self.filled_slots += 1
        
        # Track gaps
        if assigned_count < count_needed:
            self.violations += (count_needed - assigned_count)
    
    def _is_available(self, staff_member: Dict, current_date: date, hour: int) -> bool:
        """Check if staff can work this slot"""
        staff_id = staff_member['staff_id']
        
        # Max hours check
        if self.staff_hours[staff_id] >= staff_member['max_hours_per_week']:
            return False
        
        # Constraints check
        return not self._violates_constraints(staff_id, current_date, hour)
    
    def _violates_constraints(self, staff_id: str, current_date: date, hour: int) -> bool:
        """Check if scheduling violates any constraints"""
        staff_constraints = [c for c in self.constraints if c['staff_id'] == staff_id]
        
        for constraint in staff_constraints:
            # PTO
            if constraint['rule_type'] == 'pto':
                pto_start = datetime.fromisoformat(constraint['pto_start_date']).date()
                pto_end = datetime.fromisoformat(constraint['pto_end_date']).date()
                if pto_start <= current_date <= pto_end:
                    return True
            
            # Recurring
            elif constraint['rule_type'] == 'recurring':
                recurrence_type = constraint.get('recurrence_type', '')
                
                # Check end date
                if constraint.get('recurrence_end_date'):
                    end_date = datetime.fromisoformat(constraint['recurrence_end_date']).date()
                    if current_date > end_date:
                        continue
                
                # Check specific types
                if recurrence_type == 'cannot_work_specific_days':
                    blocked_days = constraint.get('blocked_days', [])
                    if blocked_days and current_date.weekday() in blocked_days:
                        return True
                
                elif recurrence_type == 'cannot_work_before_time':
                    if hour < 12:
                        return True
                
                elif recurrence_type == 'cannot_work_after_time':
                    if hour >= 22:
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
    
    def _assign_shift(self, staff_member: Dict, shift_date: date, hour: int):
        """Create and track a shift"""
        shift = {
            'staff_id': staff_member['staff_id'],
            'date': shift_date.isoformat(),
            'start_time': f"{hour:02d}:00:00",
            'end_time': f"{hour+1:02d}:00:00",
            'position': staff_member['position'],
            'hourly_rate': float(staff_member['hourly_rate']),
            'efficiency_multiplier': float(staff_member.get('efficiency_multiplier', 1.0))
        }
        
        self.all_shifts.append(shift)
        self.staff_hours[staff_member['staff_id']] += 1
        self.total_cost += float(staff_member['hourly_rate'])
    
    def _convert_covers_to_staff_by_role(self) -> Dict:
        """
        Convert covers per hour to staff needed per role
        
        Input: 
            {'weekday': {12: 60, 13: 60}, 'weekend': {12: 75}}
        
        Output:
            {'weekday': {12: {'Server': 6, 'Cook': 4, 'Host': 2, ...}}}
        
        Assumption: 4 covers per staff member baseline
        """
        staff_demand = {}
        
        for day_type in self.covers_demand:
            staff_demand[day_type] = {}
            
            for hour, covers in self.covers_demand[day_type].items():
                # Calculate total staff needed (4 covers per person)
                total_staff = max(1, round(covers / 4))
                
                # Distribute by role ratios
                staff_demand[day_type][hour] = {}
                for role, ratio in self.role_ratios.items():
                    count = max(1, round(total_staff * ratio))
                    staff_demand[day_type][hour][role] = count
        
        return staff_demand
    
    def _cost_effectiveness(self, staff_member: Dict) -> float:
        """
        Calculate cost per efficiency unit (lower is better)
        
        Formula: hourly_rate / efficiency_multiplier
        
        Example:
        - Staff A: $15/hr, 1.2x efficiency = 15/1.2 = 12.5 (better)
        - Staff B: $18/hr, 1.0x efficiency = 18/1.0 = 18.0 (worse)
        """
        rate = float(staff_member['hourly_rate'])
        eff = float(staff_member.get('efficiency_multiplier', 1.0))
        return rate / eff if eff > 0 else 999999.0
    
    def _group_by_position(self, staff: List[Dict]) -> Dict[str, List[Dict]]:
        """Group staff by position/role"""
        by_position = {}
        for s in staff:
            position = s['position']
            if position not in by_position:
                by_position[position] = []
            by_position[position].append(s)
        return by_position
    
    def _default_ratios(self) -> Dict[str, float]:
        """Default role ratios if not set"""
        return {
            "Server": 0.40,
            "Cook": 0.25,
            "Host": 0.10,
            "Busser": 0.15,
            "Bartender": 0.10
        }
    
    def _build_result(self) -> Dict:
        """Package results"""
        coverage = (self.filled_slots / self.total_demand_slots * 100) if self.total_demand_slots > 0 else 0

        # Calculate true efficiency: weighted by staff efficiency multipliers
        efficiency = self._calculate_staff_efficiency()
        
        return {
        "shifts": self.all_shifts,
        "coverage_percent": round(coverage, 1),
        "efficiency_percent": round(efficiency, 1),
        "estimated_cost": round(self.total_cost, 2),
        "total_hours": round(sum(self.staff_hours.values()), 1),
        "constraint_violations": self.violations,
        "has_coverage_gaps": self.violations > 0
    }

    def _calculate_staff_efficiency(self) -> float:
        """
        Calculate how efficiently we used staff
        
        Efficiency = average efficiency multiplier of assigned staff
        Higher = better (we're using top performers)
        """
        if not self.all_shifts:
            return 0.0
        
        total_efficiency = sum(shift['efficiency_multiplier'] for shift in self.all_shifts)
        avg_efficiency = total_efficiency / len(self.all_shifts)
        
        # Convert to percentage (1.0 = 100%, 1.2 = 120%)
        return avg_efficiency * 100
    