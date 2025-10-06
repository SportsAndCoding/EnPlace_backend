from typing import List, Dict, Tuple
from datetime import datetime, date, timedelta
import random

class ScheduleOptimizer:
    """
    Real-world restaurant shift scheduling algorithm
    
    Based on industry research:
    - 60% of shifts are 4, 6, or 8 hours
    - FOH averages 5-6 hours, BOH averages 6-8 hours
    - Split shifts (lunch + dinner) are common
    - Minimum 4-hour shifts to avoid reporting time pay penalties
    """
    
    # Real-world shift templates by type
    SHIFT_TEMPLATES = {
        # Single meal services (4-5 hours) - Most common for FOH
        'breakfast': {'start': 9, 'end': 13, 'length': 4, 'type': 'single'},
        'lunch': {'start': 11, 'end': 15, 'length': 4, 'type': 'single'},
        'dinner': {'start': 17, 'end': 21, 'length': 4, 'type': 'single'},
        'late_dinner': {'start': 18, 'end': 23, 'length': 5, 'type': 'single'},
        
        # Extended shifts (6-8 hours) - Common for BOH and full-time FOH
        'lunch_extended': {'start': 11, 'end': 17, 'length': 6, 'type': 'extended'},
        'dinner_extended': {'start': 16, 'end': 23, 'length': 7, 'type': 'extended'},
        'mid_day': {'start': 13, 'end': 19, 'length': 6, 'type': 'extended'},
        'full_day': {'start': 11, 'end': 19, 'length': 8, 'type': 'extended'},
        
        # Split shifts (lunch + dinner same day) - Common for servers
        'split_lunch': {'start': 11, 'end': 15, 'length': 4, 'type': 'split_part'},
        'split_dinner': {'start': 17, 'end': 22, 'length': 5, 'type': 'split_part'},
        
        # Manager/supervisor shifts (8-10 hours)
        'manager_open': {'start': 9, 'end': 17, 'length': 8, 'type': 'management'},
        'manager_close': {'start': 14, 'end': 23, 'length': 9, 'type': 'management'},
        'manager_mid': {'start': 11, 'end': 21, 'length': 10, 'type': 'management'},
    }

    # Map generic role categories to actual position titles
    POSITION_ALIASES = {
        'Cook': ['Line Cook', 'Prep Cook', 'Sous Chef', 'Executive Chef'],
        'Server': ['Server'],
        'Host': ['Host'],
        'Busser': ['Busser'],
        'Bartender': ['Bartender'],
        'Manager': ['Manager', 'General Manager', 'Assistant Manager']
    }
    
    # Position preferences for shift types
    POSITION_SHIFT_PREFERENCES = {
        'Server': ['lunch', 'dinner', 'split_lunch', 'split_dinner', 'dinner_extended'],
        'Host': ['lunch', 'dinner', 'lunch_extended', 'dinner_extended'],
        'Busser': ['dinner', 'late_dinner', 'dinner_extended'],
        'Bartender': ['dinner', 'late_dinner', 'dinner_extended', 'full_day'],
        'Cook': ['lunch_extended', 'dinner_extended', 'full_day', 'manager_mid'],
        'Line Cook': ['lunch_extended', 'dinner_extended', 'full_day'],
        'Prep Cook': ['breakfast', 'lunch_extended', 'mid_day'],
        'Dishwasher': ['dinner_extended', 'late_dinner', 'full_day'],
        'Sous Chef': ['lunch_extended', 'dinner_extended', 'full_day', 'manager_mid'],
        'Executive Chef': ['manager_open', 'manager_mid', 'manager_close'],
        'Manager': ['manager_open', 'manager_close', 'manager_mid'],
        'General Manager': ['manager_open', 'manager_mid', 'manager_close'],
        'Assistant Manager': ['manager_open', 'manager_close', 'manager_mid'],
    }
    
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
        self.staff_shifts_today = {}  # Track who's already working today (for split shifts)
        
        # Tracking
        self.all_shifts = []
        self.total_demand_slots = 0
        self.filled_slots = 0
        self.violations = 0
        self.total_cost = 0.0
    
    def run(self) -> Dict:
        """Execute shift-based optimization"""
        
        print("\n" + "="*80)
        print("OPTIMIZATION DEBUG LOG")
        print("="*80)
        
        # Convert covers to staff demand by role
        staff_demand = self._convert_covers_to_staff_by_role()
        print(f"\n1. COVERS → STAFF CONVERSION")
        print(f"Sample weekday demand at 12 PM: {staff_demand.get('weekday', {}).get(12, {})}")
        print(f"Sample weekday demand at 6 PM: {staff_demand.get('weekday', {}).get(18, {})}")
        
        # Schedule each day
        days = (self.pay_period_end - self.pay_period_start).days + 1
        print(f"\n2. PAY PERIOD: {self.pay_period_start} to {self.pay_period_end} ({days} days)")
        
        total_shifts_attempted = 0
        total_shifts_created = 0
        
        for day_offset in range(days):
            current_date = self.pay_period_start + timedelta(days=day_offset)
            self.staff_shifts_today = {}
            
            print(f"\n--- DAY {day_offset + 1}: {current_date} ({current_date.strftime('%A')}) ---")
            
            # Determine shifts needed for this day
            shifts_needed = self._determine_shifts_for_day(staff_demand, current_date)
            print(f"Shifts needed: {shifts_needed}")
            
            if not shifts_needed:
                print("  WARNING: No shifts determined for this day!")
                continue
            
            # Schedule each shift type
            for shift_name, roles_needed in shifts_needed.items():
                print(f"\n  Shift: {shift_name}")
                for role, count in roles_needed.items():
                    print(f"    {role}: need {count}")
                    
                    # Count available staff before scheduling
                    role_staff = self.staff_by_position.get(role, [])
                    available_before = len([
                        s for s in role_staff 
                        if self._can_work_shift(s, current_date, 
                            self.SHIFT_TEMPLATES[shift_name]['start'],
                            self.SHIFT_TEMPLATES[shift_name]['end'])
                    ])
                    
                    print(f"      Available staff: {available_before} / {len(role_staff)} total")
                    
                    total_shifts_attempted += count
                    shifts_before = len(self.all_shifts)
                    
                    self._schedule_shifts_for_role(role, count, current_date, shift_name)
                    
                    shifts_after = len(self.all_shifts)
                    shifts_created_this_call = shifts_after - shifts_before
                    total_shifts_created += shifts_created_this_call
                    
                    print(f"      Scheduled: {shifts_created_this_call} / {count} needed")
                    
                    if shifts_created_this_call < count:
                        print(f"      GAP: {count - shifts_created_this_call} unfilled")
        
        print(f"\n" + "="*80)
        print(f"SUMMARY")
        print(f"="*80)
        print(f"Total shifts attempted: {total_shifts_attempted}")
        print(f"Total shifts created: {total_shifts_created}")
        print(f"Gap: {total_shifts_attempted - total_shifts_created}")
        print(f"Unique staff used: {len([k for k, v in self.staff_hours.items() if v > 0])}")
        print("="*80 + "\n")
        
        return self._build_result()
    
    def _determine_shifts_for_day(self, staff_demand: Dict, current_date: date) -> Dict:
        """
        Analyze demand and decide which shift types to create for this day
        
        Returns: {
            'lunch': {'Server': 3, 'Cook': 2},
            'dinner': {'Server': 5, 'Cook': 3}
        }
        """
        day_type = 'weekend' if current_date.weekday() >= 5 else 'weekday'
        hourly_demand = {}
        
        # Collect all hourly demand for this day
        for hour in range(9, 24):
            role_demand = staff_demand.get(day_type, {}).get(hour, {})
            for role, count in role_demand.items():
                if role not in hourly_demand:
                    hourly_demand[role] = {}
                hourly_demand[role][hour] = count
        
        shifts_needed = {}
        
        # Analyze demand patterns and assign appropriate shift types
        for role in hourly_demand:
            # Breakfast/Opening (9-13)
            breakfast_demand = sum(hourly_demand[role].get(h, 0) for h in range(9, 13))
            if breakfast_demand > 0:
                avg_breakfast = breakfast_demand / 4
                if 'breakfast' not in shifts_needed:
                    shifts_needed['breakfast'] = {}
                shifts_needed['breakfast'][role] = max(1, round(avg_breakfast))
            
            # Lunch (11-15)
            lunch_demand = sum(hourly_demand[role].get(h, 0) for h in range(11, 15))
            if lunch_demand > 0:
                avg_lunch = lunch_demand / 4
                if 'lunch' not in shifts_needed:
                    shifts_needed['lunch'] = {}
                shifts_needed['lunch'][role] = max(1, round(avg_lunch))
            
            # Dinner (17-21)
            dinner_demand = sum(hourly_demand[role].get(h, 0) for h in range(17, 21))
            if dinner_demand > 0:
                avg_dinner = dinner_demand / 4
                if 'dinner' not in shifts_needed:
                    shifts_needed['dinner'] = {}
                shifts_needed['dinner'][role] = max(1, round(avg_dinner))
            
            # Late dinner/closing (18-23)
            late_demand = sum(hourly_demand[role].get(h, 0) for h in range(18, 23))
            if late_demand > 0:
                avg_late = late_demand / 5
                if 'late_dinner' not in shifts_needed:
                    shifts_needed['late_dinner'] = {}
                shifts_needed['late_dinner'][role] = max(1, round(avg_late))
        
        return shifts_needed
    
    def _schedule_shifts_for_role(self, role: str, count_needed: int, current_date: date, shift_name: str):
        """Schedule staff for a specific shift with balanced utilization"""
        shift_template = self.SHIFT_TEMPLATES[shift_name]
        start_hour = shift_template['start']
        end_hour = shift_template['end']
        shift_length = shift_template['length']
        
        self.total_demand_slots += count_needed
        
        # Get staff for this role
        role_staff = self.staff_by_position.get(role, [])
        
        # Filter available staff
        available = []
        for s in role_staff:
            if self._can_work_shift(s, current_date, start_hour, end_hour):
                # Calculate utilization percentage
                pay_period_days = (self.pay_period_end - self.pay_period_start).days + 1
                pay_period_weeks = pay_period_days / 7
                max_hours = s['max_hours_per_week'] * pay_period_weeks
                current_hours = self.staff_hours[s['staff_id']]
                utilization = current_hours / max_hours if max_hours > 0 else 0
                
                available.append({
                    'staff': s,
                    'utilization': utilization
                })
        
        # Sort by LOWEST utilization first (balance workload)
        available.sort(key=lambda x: (x['utilization'], self._cost_effectiveness(x['staff'])))
        
        # Assign staff
        assigned_count = min(len(available), count_needed)
        
        for i in range(assigned_count):
            staff_member = available[i]['staff']
            
            # Stagger start times
            stagger = random.choice([-15, 0, 15]) if i > 0 else 0
            actual_start = start_hour + (stagger / 60)
            actual_end = end_hour + (stagger / 60)
            
            self._assign_shift(staff_member, current_date, actual_start, actual_end)
            self.filled_slots += 1
            self.staff_shifts_today[staff_member['staff_id']] = True
        
        # Track unfilled demand
        if assigned_count < count_needed:
            self.violations += (count_needed - assigned_count)
    
    def _can_work_shift(self, staff_member: Dict, current_date: date, start_hour: int, end_hour: int) -> bool:
        """Check if staff can work entire shift"""
        staff_id = staff_member['staff_id']
        shift_length = end_hour - start_hour
        
        # Calculate max hours for pay period
        pay_period_days = (self.pay_period_end - self.pay_period_start).days + 1
        pay_period_weeks = pay_period_days / 7
        max_hours_for_period = staff_member['max_hours_per_week'] * pay_period_weeks
        
        # Check max hours
        if self.staff_hours[staff_id] + shift_length > max_hours_for_period:
            print(f"      REJECTED {staff_id}: would exceed max hours ({self.staff_hours[staff_id]} + {shift_length} > {max_hours_for_period})")
            return False
        
        # Check all constraints for every hour in shift
        for hour in range(int(start_hour), int(end_hour)):
            if self._violates_constraints(staff_id, current_date, hour):
                print(f"      REJECTED {staff_id}: constraint violation at hour {hour}")
                return False
        
        return True
    
    def _assign_shift(self, staff_member: Dict, shift_date: date, start_hour: float, end_hour: float):
        """Create shift record"""
        shift_length = end_hour - start_hour
        
        # Convert float hours to HH:MM format
        start_time = f"{int(start_hour):02d}:{int((start_hour % 1) * 60):02d}:00"
        end_time = f"{int(end_hour):02d}:{int((end_hour % 1) * 60):02d}:00"
        
        shift = {
            'staff_id': staff_member['staff_id'],
            'date': shift_date.isoformat(),
            'start_time': start_time,
            'end_time': end_time,
            'position': staff_member['position'],
            'hourly_rate': float(staff_member['hourly_rate']),
            'efficiency_multiplier': float(staff_member.get('efficiency_multiplier', 1.0))
        }
        
        self.all_shifts.append(shift)
        self.staff_hours[staff_member['staff_id']] += shift_length
        self.total_cost += float(staff_member['hourly_rate']) * shift_length
    
    def _violates_constraints(self, staff_id: str, current_date: date, hour: int) -> bool:
        """Check scheduling constraints"""
        staff_constraints = [c for c in self.constraints if c['staff_id'] == staff_id]
        
        for constraint in staff_constraints:
            if constraint['rule_type'] == 'pto':
                pto_start = datetime.fromisoformat(constraint['pto_start_date']).date()
                pto_end = datetime.fromisoformat(constraint['pto_end_date']).date()
                if pto_start <= current_date <= pto_end:
                    return True
            
            elif constraint['rule_type'] == 'recurring':
                recurrence_type = constraint.get('recurrence_type', '')
                
                if constraint.get('recurrence_end_date'):
                    end_date = datetime.fromisoformat(constraint['recurrence_end_date']).date()
                    if current_date > end_date:
                        continue
                
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
    
    def _convert_covers_to_staff_by_role(self) -> Dict:
        """Convert covers per hour to staff needed per role"""
        staff_demand = {}
        
        for day_type in self.covers_demand:
            staff_demand[day_type] = {}
            
            for hour, covers in self.covers_demand[day_type].items():
                total_staff = max(1, round(covers / 4))
                
                staff_demand[day_type][hour] = {}
                for role, ratio in self.role_ratios.items():
                    count = max(1, round(total_staff * ratio))
                    staff_demand[day_type][hour][role] = count
        
        return staff_demand
    
    def _cost_effectiveness(self, staff_member: Dict) -> float:
        """Calculate cost per efficiency unit"""
        rate = float(staff_member['hourly_rate'])
        eff = float(staff_member.get('efficiency_multiplier', 1.0))
        return rate / eff if eff > 0 else 999999.0
    
    def _group_by_position(self, staff: List[Dict]) -> Dict[str, List[Dict]]:
        """Group staff by role category using position aliases"""
        by_position = {}
        
        for s in staff:
            actual_position = s['position']
            
            # Find which role category this position belongs to
            for role, aliases in self.POSITION_ALIASES.items():
                if actual_position in aliases:
                    if role not in by_position:
                        by_position[role] = []
                    by_position[role].append(s)
                    break  # CRITICAL: Stop after first match to avoid duplicates
            else:
                # Only reaches here if no alias matched
                if actual_position not in by_position:
                    by_position[actual_position] = []
                by_position[actual_position].append(s)
        
        return by_position
    
    def _default_ratios(self) -> Dict[str, float]:
        """Default role ratios"""
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
        avg_cost_per_shift = (self.total_cost / len(self.all_shifts)) if self.all_shifts else 0
        
        return {
            "shifts": self.all_shifts,
            "coverage_percent": round(coverage, 1),
            "avg_cost_per_shift": round(avg_cost_per_shift, 2),
            "estimated_cost": round(self.total_cost, 2),
            "total_hours": round(sum(self.staff_hours.values()), 1),
            "constraint_violations": self.violations,
            "has_coverage_gaps": self.violations > 0
        }