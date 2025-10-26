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
    

    # Map generic role categories to actual position titles
    POSITION_ALIASES = {
        'Cook': ['Line Cook', 'Prep Cook', 'Sous Chef', 'Executive Chef', 'Dishwasher'],
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
        pay_period_end: str,
        allow_overtime: bool = False
    ):
        self.staffing_ratios = restaurant_settings.get('staffing_ratios', {
            'Server': 12,
            'Cook': 25,
            'Host': 30,
            'Busser': 25,
            'Bartender': 15
        })
        self.operating_hours = restaurant_settings.get('operating_hours', {
            'open_hour': 9,
            'close_hour': 24,
            'spans_midnight': True
        })
        
        # Load operating settings (opening/closing crew)
        restaurant_id = restaurant_settings.get('id')
        if restaurant_id:
            self.operating_settings = self._load_operating_settings(restaurant_id)
        else:
            # Use defaults if restaurant_id not available
            self.operating_settings = {
                'prep_start_time': '06:00:00',
                'prep_positions': ['Prep Cook', 'Sous Chef'],
                'prep_staff_count': 2,
                'doors_open_time': '09:00:00',
                'doors_close_time': '22:00:00',
                'last_seating_time': '21:30:00',
                'kitchen_close_time': '23:00:00',
                'cleanup_positions': ['Dishwasher', 'Line Cook', 'Manager'],
                'cleanup_staff_count': 3
            }
        
        self.SHIFT_TEMPLATES = self._build_shift_templates(self.operating_hours['open_hour'], self.operating_hours['close_hour'])

        self.restaurant = restaurant_settings
        self.staff = staff
        self.constraints = constraints
        self.covers_demand = covers_demand
        self.pay_period_start = datetime.fromisoformat(pay_period_start).date()
        self.pay_period_end = datetime.fromisoformat(pay_period_end).date()
        self.allow_overtime = allow_overtime
        
        # Derived data
        self.role_ratios = restaurant_settings.get('role_ratios', self._default_ratios())
        self.staff_by_position = self._group_by_position(staff)
        self.staff_hours = {s['staff_id']: 0 for s in staff}
        self.staff_shifts_today = {}
        
        # Tracking
        self.all_shifts = []
        self.total_demand_slots = 0
        self.filled_slots = 0
        self.violations = 0
        self.total_cost = 0.0
    
    def _load_operating_settings(self, restaurant_id: int) -> Dict:
        """Load restaurant operating hours and opening/closing crew settings"""
        from database.supabase_client import get_supabase
        
        supabase = get_supabase()
        
        result = supabase.table('restaurant_operating_settings')\
            .select('*')\
            .eq('restaurant_id', restaurant_id)\
            .execute()
        
        if not result.data:
            # Return defaults if not configured
            return {
                'prep_start_time': '06:00:00',
                'prep_positions': ['Prep Cook', 'Sous Chef'],
                'prep_staff_count': 2,
                'doors_open_time': '09:00:00',
                'doors_close_time': '22:00:00',
                'last_seating_time': '21:30:00',
                'kitchen_close_time': '23:00:00',
                'cleanup_positions': ['Dishwasher', 'Line Cook', 'Manager'],
                'cleanup_staff_count': 3
            }
        
        return result.data[0]
    
    def _identify_peaks(self, hourly_demand: Dict[int, int]) -> List[Dict]:
        """
        Find local maxima (peaks) in demand curve
        Returns list of {hour, demand} dicts
        """
        if not hourly_demand:
            return []
        
        peaks = []
        hours = sorted(hourly_demand.keys())
        
        # Need at least 3 hours to detect peaks
        if len(hours) < 3:
            return []
        
        for i in range(1, len(hours) - 1):
            curr_hour = hours[i]
            prev_demand = hourly_demand.get(hours[i-1], 0)
            curr_demand = hourly_demand.get(curr_hour, 0)
            next_demand = hourly_demand.get(hours[i+1], 0)
            
            # Handle case where demand might be a dict (extract value)
            if isinstance(prev_demand, dict):
                prev_demand = 0
            if isinstance(next_demand, dict):
                next_demand = 0
            
            # Peak = higher than previous AND at-or-above next (catches plateau starts)
            if curr_demand > prev_demand and curr_demand >= next_demand and curr_demand >= 2:
                peaks.append({
                    'hour': curr_hour,
                    'demand': curr_demand
                })
        
        return peaks


    def _create_wave_shifts(self, role: str, hourly_demand: Dict[int, int], current_date: date) -> List[Dict]:
        """
        Create shifts using wave/onion layer approach instead of templates
        
        Returns list of shift specs: {start_hour, end_hour, count, type}
        """
        shifts = []
        
        # Layer 0: Opening crew (prep time)
        if role == 'Cook':
            prep_start = int(self.operating_settings['prep_start_time'].split(':')[0])
            doors_open = int(self.operating_settings['doors_open_time'].split(':')[0])
            
            if prep_start < doors_open:
                shifts.append({
                    'start_hour': prep_start,
                    'end_hour': doors_open + 6,  # 6h prep shift
                    'count': self.operating_settings['prep_staff_count'],
                    'type': 'opening_prep'
                })
        
        # Layer 1: Identify peaks and valleys
        peaks = self._identify_peaks(hourly_demand)
        min_demand = self._get_minimum_demand(hourly_demand)
        print(f"\nDEBUG {role} WAVE ANALYSIS:")
        print(f"  hourly_demand = {hourly_demand}")
        print(f"DEBUG {role}: peaks = {peaks}")
        print(f"DEBUG {role}: min_demand = {min_demand}")
        
        
        # Layer 2: Base layer (covers minimum demand throughout the day)
        doors_open = int(self.operating_settings['doors_open_time'].split(':')[0])
        doors_close = int(self.operating_settings['doors_close_time'].split(':')[0])
        
        if min_demand > 0:
            # Create base shifts that span the entire operating day
            # Split into 2 shifts for better distribution (e.g., 9-5 and 2-10)
            if (doors_close - doors_open) > 10:
                # Long day: create overlapping shifts
                mid_point = doors_open + ((doors_close - doors_open) // 2)
                
                shifts.append({
                    'start_hour': doors_open,
                    'end_hour': mid_point + 3,  # 8-9h shift
                    'count': max(1, min_demand // 2),
                    'type': 'base_morning'
                })
                
                shifts.append({
                    'start_hour': mid_point - 1,
                    'end_hour': doors_close,  # 8-9h shift
                    'count': max(1, min_demand - (min_demand // 2)),
                    'type': 'base_evening'
                })
            else:
                # Short day: one base shift
                shifts.append({
                    'start_hour': doors_open,
                    'end_hour': doors_close,
                    'count': min_demand,
                    'type': 'base_full'
                })
        
        # Layer 3: Peak booster waves
        for peak in peaks:
            extra_needed = peak['demand'] - min_demand
            
            if extra_needed > 0:
                # Arrive exactly when demand rises, leave when it drops
                # Find when demand rises to near-peak levels
                wave_start = peak['hour']
                for h in range(max(doors_open, peak['hour'] - 2), peak['hour']):
                    if hourly_demand.get(h, 0) >= peak['demand'] - 1:
                        wave_start = h
                        break
            
            # Find when demand drops below near-peak
            wave_end = peak['hour'] + 1
            for h in range(peak['hour'] + 1, min(doors_close + 1, peak['hour'] + 4)):
                if hourly_demand.get(h, 0) < peak['demand'] - 1:
                    wave_end = h
                    break
                
                shifts.append({
                    'start_hour': wave_start,
                    'end_hour': wave_end,
                    'count': extra_needed,
                    'type': f'peak_boost_{peak["hour"]}h'
                })
        
        # Layer 4: Closing crew
        if role in self.operating_settings['cleanup_positions']:
            kitchen_close = int(self.operating_settings['kitchen_close_time'].split(':')[0])
            last_seating = int(self.operating_settings['last_seating_time'].split(':')[0])
            
            shifts.append({
                'start_hour': last_seating - 2,  # Arrive 2h before last seating
                'end_hour': kitchen_close,
                'count': 1,  # Usually 1 per position for closing
                'type': 'closing'
            })
        
        print(f"  Created {len(shifts)} wave shifts for {role}:")
        for s in shifts:
            print(f"    - {s['type']}: {s['start_hour']}-{s['end_hour']} ({s['count']} staff)")

        return shifts


    def _identify_valleys(self, hourly_demand: Dict[int, int]) -> List[Dict]:
        """
        Find local minima (valleys) in demand curve
        Returns list of {hour, demand} dicts
        """
        if not hourly_demand:
            return []
        
        valleys = []
        hours = sorted(hourly_demand.keys())
        
        if len(hours) < 3:
            return []
        
        for i in range(1, len(hours) - 1):
            curr_hour = hours[i]
            prev_demand = hourly_demand.get(hours[i-1], 0)
            curr_demand = hourly_demand.get(curr_hour, 0)
            next_demand = hourly_demand.get(hours[i+1], 0)
            
            # Valley = lower than both neighbors
            if curr_demand <= prev_demand and curr_demand <= next_demand:
                valleys.append({
                    'hour': curr_hour,
                    'demand': curr_demand
                })
        
        return valleys


    def _get_minimum_demand(self, hourly_demand: Dict[int, int]) -> int:
        """Get the minimum staffing level needed (base layer)"""
        if not hourly_demand:
            return 0
        return min(hourly_demand.values())
    
    def _build_shift_templates(self, open_hour: int, close_hour: int) -> Dict:
        """Build shift templates based on restaurant operating hours"""
        templates = {}
        
        # Breakfast (if open early enough)
        if open_hour <= 9:  # ONLY create breakfast if open by 9 AM
            templates['breakfast'] = {'start': open_hour, 'end': open_hour + 4, 'length': 4, 'type': 'single'}
        
        # Lunch (only if open by 11 AM)
        if open_hour <= 11:
            templates['lunch'] = {'start': 11, 'end': 15, 'length': 4, 'type': 'single'}
        
        # Afternoon bridge (only if open by 2 PM)
        if open_hour <= 14:
            templates['afternoon'] = {'start': 14, 'end': 18, 'length': 4, 'type': 'single'}
        
        # Dinner (always, but adjust start time if opening later)
        dinner_start = max(open_hour, 17)
        templates['dinner'] = {'start': dinner_start, 'end': min(21, close_hour), 'length': min(4, close_hour - dinner_start), 'type': 'single'}
        
        # Late dinner
        late_start = max(open_hour, 18)
        templates['late_dinner'] = {'start': late_start, 'end': min(23, close_hour), 'length': min(5, close_hour - late_start), 'type': 'single'}
        
        # Closing shift (can span midnight)
        if close_hour >= 23:
            closing_start = max(open_hour, 19)
            templates['closing'] = {'start': closing_start, 'end': close_hour, 'length': close_hour - closing_start, 'type': 'single'}
        
        # Extended shifts (only if enough operating hours)
        if close_hour - open_hour >= 6:
            templates['dinner_extended'] = {'start': max(open_hour, 16), 'end': min(23, close_hour), 'length': 6, 'type': 'extended'}
        
        if close_hour - open_hour >= 8:
            templates['full_day'] = {'start': open_hour, 'end': min(open_hour + 8, close_hour), 'length': 8, 'type': 'extended'}
        
        # Management shifts
        templates['manager_open'] = {'start': open_hour, 'end': min(open_hour + 8, close_hour), 'length': min(8, close_hour - open_hour), 'type': 'management'}
        templates['manager_close'] = {'start': max(open_hour, close_hour - 9), 'end': close_hour, 'length': min(9, close_hour - open_hour), 'type': 'management'}
        
        return templates


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
        print(f"\nDEBUG CONVERSION at 6 PM weekday:")
        print(f"  Covers: {self.covers_demand.get('weekday', {}).get(18, 'N/A')}")
        print(f"  Staff demand: {staff_demand.get('weekday', {}).get(18, {})}")
        
        # Schedule each day
        days = (self.pay_period_end - self.pay_period_start).days + 1
        print(f"\n2. PAY PERIOD: {self.pay_period_start} to {self.pay_period_end} ({days} days)")
        
        total_shifts_attempted = 0
        total_shifts_created = 0
        
        for day_offset in range(days):
            current_date = self.pay_period_start + timedelta(days=day_offset)
            self.staff_shifts_today = {}  # Reset: staff_id → [(start, end), (start, end), ...]
            
            print(f"\n--- DAY {day_offset + 1}: {current_date} ({current_date.strftime('%A')}) ---")
            
            # Extract demand for this specific day type and restructure
            day_type = 'weekend' if current_date.weekday() >= 5 else 'weekday'
            day_demand = staff_demand.get(day_type, {})
            
            # Restructure from {hour: {role: count}} to {role: {hour: count}}
            demand_by_role = {}
            for hour, roles in day_demand.items():
                for role, count in roles.items():
                    if role not in demand_by_role:
                        demand_by_role[role] = {}
                    demand_by_role[role][hour] = count
            
            # Determine shifts needed for this day (wave-based)
            shifts_needed = self._determine_shifts_for_day(demand_by_role, current_date)
            print(f"Wave shifts needed: {shifts_needed}")
            
            if not shifts_needed:
                print("  WARNING: No shifts determined for this day!")
                continue
            
            # Schedule each role's wave shifts
            for role, wave_shifts in shifts_needed.items():
                print(f"\n  Role: {role}")
                for shift_key, shift_spec in wave_shifts.items():
                    print(f"    Shift: {shift_spec['type']} ({shift_spec['start_hour']}-{shift_spec['end_hour']}h)")
                    print(f"    Need: {shift_spec['count']}")
                    
                    # Count available staff before scheduling
                    role_staff = self.staff_by_position.get(role, [])
                    available_before = len([
                        s for s in role_staff 
                        if self._can_work_shift(s, current_date, 
                            shift_spec['start_hour'],
                            shift_spec['end_hour'])
                    ])
                    
                    print(f"      Available staff: {available_before} / {len(role_staff)} total")
                    
                    total_shifts_attempted += count
                    shifts_before = len(self.all_shifts)
                    
                    self._schedule_shifts_for_role(role, shift_spec['count'], current_date, shift_spec)
                    
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

        # Print per-staff schedule summary
        print("="*80)
        print("STAFF SCHEDULE SUMMARY")
        print("="*80)

        # Group shifts by staff
        staff_schedules = {}
        for shift in self.all_shifts:
            staff_id = shift['staff_id']
            if staff_id not in staff_schedules:
                staff_schedules[staff_id] = []
            staff_schedules[staff_id].append(shift)

        # Sort staff by total hours worked (descending)
        staff_with_hours = [(sid, self.staff_hours[sid]) for sid in staff_schedules.keys()]
        staff_with_hours.sort(key=lambda x: x[1], reverse=True)

        # Print each staff member's schedule
        for staff_id, total_hours in staff_with_hours:
            # Get staff details
            staff_member = next((s for s in self.staff if s['staff_id'] == staff_id), None)
            if not staff_member:
                continue
            
            name = staff_member.get('full_name', 'Unknown')
            position = staff_member.get('position', 'Unknown')
            max_hours_week = staff_member.get('max_hours_per_week', 40)
            
            shifts = staff_schedules[staff_id]
            shifts.sort(key=lambda s: s['date'])  # Sort by date
            
            print(f"\n{name} ({position}) - {staff_id}")
            print(f"  Max: {max_hours_week}h/week ({max_hours_week * 2}h/period)")
            print(f"  Worked: {total_hours:.1f}h total")
            print(f"  Shifts: {len(shifts)}")
            
            # Group by week
            week1_shifts = [s for s in shifts if s['date'] < '2025-11-03']  # First 7 days
            week2_shifts = [s for s in shifts if s['date'] >= '2025-11-03']  # Last 7 days
            
            week1_hours = sum(self._calculate_shift_hours(s) for s in week1_shifts)
            week2_hours = sum(self._calculate_shift_hours(s) for s in week2_shifts)
            
            print(f"  Week 1: {len(week1_shifts)} shifts, {week1_hours:.1f}h")
            print(f"  Week 2: {len(week2_shifts)} shifts, {week2_hours:.1f}h")
            
            # Show schedule
            print(f"  Schedule:")
            for shift in shifts:
                date = shift['date']
                start = shift['start_time'][:5]  # HH:MM
                end = shift['end_time'][:5]
                hours = self._calculate_shift_hours(shift)
                print(f"    {date}: {start}-{end} ({hours:.1f}h)")

        print("\n" + "="*80)
        print("END STAFF SUMMARY")
        print("="*80 + "\n")

        return self._build_result()
    
    def _determine_shifts_for_day(self, staff_demand: Dict[str, Dict[int, int]], current_date: date) -> Dict[str, Dict[str, int]]:
        """
        Determine shifts needed using wave-based approach
        Returns: {role: {shift_key: count}}
        """
        shifts_needed = {}
        
        for role, hourly_demand in staff_demand.items():
            if not hourly_demand:
                continue
            
            # Generate wave-based shifts for this role
            wave_shifts = self._create_wave_shifts(role, hourly_demand, current_date)
            
            # Convert to shift keys for tracking
            role_shifts = {}
            for i, shift_spec in enumerate(wave_shifts):
                shift_key = f"{shift_spec['type']}_{shift_spec['start_hour']}_{shift_spec['end_hour']}"
                role_shifts[shift_key] = {
                    'count': shift_spec['count'],
                    'start_hour': shift_spec['start_hour'],
                    'end_hour': shift_spec['end_hour'],
                    'type': shift_spec['type']
                }
            
            if role_shifts:
                shifts_needed[role] = role_shifts
        
        return shifts_needed
    
    def utilization_band(util):
            if util < 0.25:
                return 0
            elif util < 0.50:
                return 1
            elif util < 0.75:
                return 2
            else:
                return 3
    
    def _schedule_shifts_for_role(self, role: str, count_needed: int, current_date: date, shift_spec: Dict):
        """Schedule staff for a specific shift with balanced utilization"""
        # Wave shifts pass the full spec instead of template name
        start_hour = shift_spec['start_hour']
        end_hour = shift_spec['end_hour']
        shift_length = end_hour - start_hour
        
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
        available.sort(key=lambda x: (x['utilization'], random.random()))
        
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
    
    def _count_consecutive_days(self, staff_id: str, current_date: date) -> int:
        """Count how many consecutive days this staff member has worked leading up to today"""
        if not hasattr(self, 'staff_days_worked_set'):
            self.staff_days_worked_set = {}
        
        if staff_id not in self.staff_days_worked_set:
            self.staff_days_worked_set[staff_id] = set()
        
        consecutive = 0
        check_date = current_date - timedelta(days=1)
        
        # Count backwards to find consecutive days
        while check_date >= self.pay_period_start:
            if check_date.isoformat() in self.staff_days_worked_set[staff_id]:
                consecutive += 1
                check_date -= timedelta(days=1)
            else:
                break
        
        return consecutive

    def _can_work_shift(self, staff_member: Dict, current_date: date, start_hour: int, end_hour: int) -> bool:
        """Check if staff can work entire shift"""
        staff_id = staff_member['staff_id']
        shift_length = end_hour - start_hour
        
        # RULE 0: Maximum days worked per period (prevent burnout)
        if not hasattr(self, 'staff_days_worked'):
            self.staff_days_worked = {}
        
        days_worked = self.staff_days_worked.get(staff_id, 0)
        max_days_per_period = 12  # Max 12 out of 14 days (gives 2 days off)
        
        if days_worked >= max_days_per_period:
            print(f"      REJECTED {staff_id}: already worked {days_worked} days (max {max_days_per_period})")
            return False
        
        # RULE 1: Only one shift per person per day (no split shifts, no overlaps)
        if staff_id in self.staff_shifts_today:
            print(f"      REJECTED {staff_id}: already scheduled today")
            return False
        
        # Calculate max hours for pay period
        pay_period_days = (self.pay_period_end - self.pay_period_start).days + 1
        pay_period_weeks = pay_period_days / 7
        max_hours_for_period = staff_member['max_hours_per_week'] * pay_period_weeks
        
        # DEBUG: Log ONCE per optimization run
        if not hasattr(self, '_logged_period_debug'):
            print(f"\nðŸŸ¡ PAY PERIOD CALCULATION:")
            print(f"   Start date: {self.pay_period_start}")
            print(f"   End date: {self.pay_period_end}")
            print(f"   Total days: {pay_period_days}")
            print(f"   Calculated weeks: {pay_period_weeks}")
            print(f"   Sample staff max_hours_per_week: {staff_member['max_hours_per_week']}")
            print(f"   Calculated max_hours_for_period: {max_hours_for_period}")
            print(f"   Allow overtime: {self.allow_overtime}")
            if self.allow_overtime:
                print(f"   Absolute max (with OT): {max_hours_for_period * 2}")
            print()
            self._logged_period_debug = True
        
        # Check max hours (only enforce if overtime is disabled)
        if not self.allow_overtime:
            if self.staff_hours[staff_id] + shift_length > max_hours_for_period:
                print(f"      REJECTED {staff_id}: would exceed max hours ({self.staff_hours[staff_id]} + {shift_length} > {max_hours_for_period})")
                return False
        else:
            # Overtime allowed: Can schedule beyond their preference
            # Soft cap at 60 hours/week (120 hours per 2-week period) to prevent burnout
            absolute_max = 60 * pay_period_weeks  # 120 hours for 2 weeks
            if self.staff_hours[staff_id] + shift_length > absolute_max:
                print(f"      REJECTED {staff_id}: would exceed absolute max ({self.staff_hours[staff_id]} + {shift_length} > {absolute_max})")
                return False
        
        # Check all constraints for every hour in shift
        for hour in range(int(start_hour), int(end_hour)):
            if self._violates_constraints(staff_id, current_date, hour):
                print(f"      REJECTED {staff_id}: constraint violation at hour {hour}")
                return False
        
        return True
    
    def _assign_shift(self, staff_member: Dict, shift_date: date, start_hour: float, end_hour: float):
        """Create shift record"""
        from datetime import timedelta
        
        shift_length = end_hour - start_hour
        
        # Handle shifts that span midnight
        shift_date_obj = shift_date
        end_date_obj = shift_date
        
        # Convert float hours to HH:MM format
        start_hour_int = int(start_hour)
        start_min_int = int((start_hour % 1) * 60)
        start_time = f"{start_hour_int:02d}:{start_min_int:02d}:00"
        
        end_hour_int = int(end_hour)
        end_min_int = int((end_hour % 1) * 60)
        
        # If end hour >= 24, it's the next day
        if end_hour_int >= 24:
            end_hour_int = end_hour_int - 24
            end_date_obj = shift_date + timedelta(days=1)
        
            
        end_time = f"{end_hour_int:02d}:{end_min_int:02d}:00"
        shift = {
            'staff_id': staff_member['staff_id'],
            'date': shift_date_obj.isoformat(),
            'start_time': start_time,
            'end_time': end_time,
            'position': staff_member['position'],
            'hourly_rate': float(staff_member['hourly_rate']),
            'efficiency_multiplier': float(staff_member.get('efficiency_multiplier', 1.0))
        }
        
        # If shift spans midnight, add metadata
        if end_hour >= 24:
            shift['spans_midnight'] = True
            shift['end_date'] = end_date_obj.isoformat()
        
        
        # NOW mark them as scheduled
        self.staff_shifts_today[staff_member['staff_id']] = True
        
        self.all_shifts.append(shift)
        self.staff_hours[staff_member['staff_id']] += shift_length
        
        # Calculate cost with overtime premium if applicable
        base_rate = float(staff_member['hourly_rate'])
        
        # Overtime is calculated PER WEEK (not per period)
        # Determine which week this shift is in (0 or 1 for 2-week period)
        shift_week = (shift_date - self.pay_period_start).days // 7
        
        # Track weekly hours per staff member
        if not hasattr(self, 'staff_weekly_hours'):
            self.staff_weekly_hours = {}
        if staff_member['staff_id'] not in self.staff_weekly_hours:
            self.staff_weekly_hours[staff_member['staff_id']] = [0.0, 0.0]  # [week0_hours, week1_hours]
        
        hours_this_week_before = self.staff_weekly_hours[staff_member['staff_id']][shift_week]
        hours_this_week_after = hours_this_week_before + shift_length
        weekly_threshold = 40.0
        
        if self.allow_overtime and hours_this_week_after > weekly_threshold:
            # Calculate how much of this shift is overtime
            if hours_this_week_before >= weekly_threshold:
                # Already over 40h this week, entire shift is OT
                ot_hours = shift_length
                regular_hours = 0
            else:
                # Crosses the 40h threshold during this shift
                regular_hours = weekly_threshold - hours_this_week_before
                ot_hours = shift_length - regular_hours
            
            shift_cost = (regular_hours * base_rate) + (ot_hours * base_rate * 1.5)
            
            shift['overtime_hours'] = round(ot_hours, 2)
            shift['effective_rate'] = round(shift_cost / shift_length, 2)
        else:
            shift_cost = base_rate * shift_length
        
        # Update weekly hours tracker
        self.staff_weekly_hours[staff_member['staff_id']][shift_week] += shift_length
        
        # Track days worked (must check BEFORE marking them as scheduled today)
        if not hasattr(self, 'staff_days_worked'):
            self.staff_days_worked = {}
        
        # Only increment if this is their first shift today
        if staff_member['staff_id'] not in self.staff_shifts_today:
            self.staff_days_worked[staff_member['staff_id']] = self.staff_days_worked.get(staff_member['staff_id'], 0) + 1
        
        self.total_cost += shift_cost

    
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
        staff_demand = {}
        
        # Use dynamic ratios from restaurant settings
        coverage_ratios = self.staffing_ratios
        
        for day_type in self.covers_demand:
            staff_demand[day_type] = {}
            
            for hour, covers in self.covers_demand[day_type].items():
                staff_demand[day_type][hour] = {}
                
                for role, covers_per_staff in coverage_ratios.items():
                    staff_needed = max(1, round(covers / covers_per_staff))
                    staff_demand[day_type][hour][role] = staff_needed
        
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
    
    def _calculate_shift_hours(self, shift: Dict) -> float:
        """Calculate shift length from shift record"""
        start_hour = int(shift['start_time'].split(':')[0])
        start_min = int(shift['start_time'].split(':')[1])
        end_hour = int(shift['end_time'].split(':')[0])
        end_min = int(shift['end_time'].split(':')[1])
        
        # Handle midnight rollover
        if end_hour == 0 and start_hour > 0:
            end_hour = 24
        
        return (end_hour + end_min/60) - (start_hour + start_min/60)