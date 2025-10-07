from database.supabase_client import get_supabase
from models.schedule_optimization import ScheduleOptimizer
from typing import List, Dict
from datetime import datetime, date



class SchedulingService:
    """
    Service layer for schedule optimization
    
    Responsibilities:
    - Load data from database
    - Orchestrate optimization
    - Save results to database
    """
    
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
        Generate optimized schedule
        
        Flow:
        1. Load restaurant settings (role ratios)
        2. Load staff (with efficiency metrics)
        3. Load constraints (PTO, recurring rules)
        4. Load demand curve (covers/hour with overrides)
        5. Run optimization algorithm
        6. Save schedule and shifts to database
        7. Return metrics
        """
        
        # 1. Load all data
        restaurant = await self._load_restaurant_settings(restaurant_id)
        staff = await self._load_staff(restaurant_id)
        constraints = await self._load_constraints(restaurant_id)
        covers_demand = await self._load_demand_curve(restaurant_id, pay_period_start, pay_period_end)
        
        # 2. Run optimization algorithm
        optimizer = ScheduleOptimizer(
            restaurant_settings=restaurant,
            staff=staff,
            constraints=constraints,
            covers_demand=covers_demand,
            pay_period_start=pay_period_start,
            pay_period_end=pay_period_end
        )
        
        result = optimizer.run()
        
        # 3. Save to database
        schedule_id = await self._save_schedule(
            restaurant_id=restaurant_id,
            pay_period_start=pay_period_start,
            pay_period_end=pay_period_end,
            shifts=result['shifts'],
            coverage_score=result['coverage_percent'],
            total_cost=result['estimated_cost'],
            total_hours=result['total_hours'],
            violations=result['constraint_violations'],
            created_by=created_by
        )
        
        # 4. Return complete result
        return {
            "schedule_id": schedule_id,
            "coverage_percent": result['coverage_percent'],
            "avg_cost_per_shift": result['avg_cost_per_shift'],
            "estimated_cost": result['estimated_cost'],
            "total_hours": result['total_hours'],
            "constraint_violations": result['constraint_violations'],
            "has_coverage_gaps": result['has_coverage_gaps']
        }
    
    # ============ DATA LOADING METHODS ============
    
    async def _load_restaurant_settings(self, restaurant_id: int) -> Dict:
        """Load restaurant with role ratios"""
        response = self.supabase.from_('restaurants') \
            .select('role_ratios') \
            .eq('id', restaurant_id) \
            .single() \
            .execute()
        
        return response.data if response.data else {}
    
    async def _load_staff(self, restaurant_id: int) -> List[Dict]:
        """Load all active staff with efficiency metrics"""
        response = self.supabase.from_('staff') \
            .select('staff_id, full_name, position, hourly_rate, max_hours_per_week, efficiency_multiplier') \
            .eq('restaurant_id', restaurant_id) \
            .eq('status', 'Active') \
            .execute()
        
        return response.data if response.data else []
    
    async def _load_constraints(self, restaurant_id: int) -> List[Dict]:
        """Load all active scheduling constraints"""
        response = self.supabase.from_('staff_scheduling_rules') \
            .select('*') \
            .eq('restaurant_id', restaurant_id) \
            .eq('is_active', True) \
            .execute()
        
        return response.data if response.data else []
    
    async def _load_demand_curve(self, restaurant_id: int, pay_period_start: str, pay_period_end: str) -> Dict:
        """
        Load covers/hour demand with pay period overrides
        
        Priority: pay_period_demand_overrides > restaurant_demand_patterns
        """
        
        # Load default patterns
        default_response = self.supabase.from_('restaurant_demand_patterns') \
            .select('day_type, hour, covers_per_hour') \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        # Build demand dictionary
        demand = {'weekday': {}, 'weekend': {}}
        for row in default_response.data or []:
            demand[row['day_type']][row['hour']] = row['covers_per_hour']
        
        # Load overrides for this pay period
        override_response = self.supabase.from_('pay_period_demand_overrides') \
            .select('*') \
            .eq('restaurant_id', restaurant_id) \
            .eq('pay_period_start', pay_period_start) \
            .eq('pay_period_end', pay_period_end) \
            .execute()
        
        # Apply overrides (these take precedence)
        for override in override_response.data or []:
            if override.get('day_type'):
                demand[override['day_type']][override['hour']] = override['covers_per_hour']
            # TODO: Handle specific_date overrides when needed
        
        return demand
    
    # ============ DATABASE SAVE ============
    
    async def _save_schedule(
        self,
        restaurant_id: int,
        pay_period_start: str,
        pay_period_end: str,
        shifts: List[Dict],
        coverage_score: float,
        total_cost: float,
        total_hours: float,
        violations: int,
        created_by: str
    ) -> str:
        """Save schedule and shifts to database"""
        
        # Create schedule record
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
                'confidence_score': float(shift['efficiency_multiplier']),
                'constraint_flags': {}
            }
            shift_records.append(shift_record)
        
        # Batch insert shifts
        if shift_records:
            self.supabase.from_('generated_shifts') \
                .insert(shift_records) \
                .execute()
        
        return schedule_id
    
    async def get_shifts(self, schedule_id: str, restaurant_id: int) -> List[Dict]:
        """Get all shifts for a schedule"""
        
        # Verify schedule belongs to this restaurant
        schedule_response = self.supabase.from_('generated_schedules') \
            .select('restaurant_id') \
            .eq('id', schedule_id) \
            .single() \
            .execute()
        
        if not schedule_response.data or schedule_response.data['restaurant_id'] != restaurant_id:
            raise Exception("Schedule not found or access denied")
        
        # Fetch shifts with staff names
        shifts_response = self.supabase.from_('generated_shifts') \
            .select('*, staff(full_name, position)') \
            .eq('generated_schedule_id', schedule_id) \
            .order('date, start_time') \
            .execute()
        
        return shifts_response.data if shifts_response.data else []
    
    async def get_latest_schedule(self, restaurant_id: int) -> Dict:
        """Get the most recently created schedule for a restaurant"""
        
        response = self.supabase.from_('generated_schedules') \
            .select('id, created_at, coverage_score, total_labor_cost, total_labor_hours, constraint_violations') \
            .eq('restaurant_id', restaurant_id) \
            .order('created_at', desc=True) \
            .limit(1) \
            .execute()
        
        if response.data and len(response.data) > 0:
            return response.data[0]
        
        return None
    
    

    async def update_schedule_shifts(
        self,
        schedule_id: str,
        restaurant_id: int,
        changes: List[Dict],
        updated_by: str
    ) -> Dict:
        """
        Apply manual edits to a schedule
        
        Args:
            schedule_id: UUID of the schedule to update
            restaurant_id: Restaurant ID (for security validation)
            changes: List of {action: "add"|"remove", ...} operations
            updated_by: staff_id of the manager making changes
        
        Returns:
            {
                "shifts_added": int,
                "shifts_removed": int,
                "updated_metrics": {...},
                "warnings": [...]
            }
        """
        
        # 1. Verify schedule belongs to this restaurant
        schedule_response = self.supabase.from_('generated_schedules') \
            .select('restaurant_id') \
            .eq('id', schedule_id) \
            .single() \
            .execute()
        
        if not schedule_response.data or schedule_response.data['restaurant_id'] != restaurant_id:
            raise Exception("Schedule not found or access denied")
        
        warnings = []
        shifts_added = 0
        shifts_removed = 0
        
        # 2. Process each change
        for change in changes:
            if change['action'] == 'remove':
                # Remove shift
                delete_response = self.supabase.from_('generated_shifts') \
                    .delete() \
                    .eq('id', change['shift_id']) \
                    .execute()
                
                if delete_response.data:
                    shifts_removed += 1
            
            elif change['action'] == 'add':
                # Validate staff exists and is active
                staff_response = self.supabase.from_('staff') \
                    .select('staff_id, full_name, position, hourly_rate, max_hours_per_week, efficiency_multiplier') \
                    .eq('staff_id', change['staff_id']) \
                    .eq('restaurant_id', restaurant_id) \
                    .eq('status', 'Active') \
                    .single() \
                    .execute()
                
                if not staff_response.data:
                    warnings.append(f"Staff {change['staff_id']} not found or inactive")
                    continue
                
                staff = staff_response.data
                
                # Check constraints (warn but don't block)
                constraint_violations = await self._check_constraints(
                    staff_id=change['staff_id'],
                    date=change['date'],
                    start_time=change['start_time'],
                    end_time=change['end_time'],
                    restaurant_id=restaurant_id
                )
                
                if constraint_violations:
                    warnings.extend(constraint_violations)
                
                # Insert new shift
                shift_data = {
                    'generated_schedule_id': schedule_id,
                    'restaurant_id': restaurant_id,
                    'staff_id': change['staff_id'],
                    'date': change['date'],
                    'start_time': change['start_time'],
                    'end_time': change['end_time'],
                    'position': change['position'],
                    'confidence_score': float(staff.get('efficiency_multiplier', 1.0)),
                    'constraint_flags': {'manual_edit': True, 'edited_by': updated_by}
                }
                
                insert_response = self.supabase.from_('generated_shifts') \
                    .insert(shift_data) \
                    .execute()
                
                if insert_response.data:
                    shifts_added += 1
        
        # 3. Recalculate schedule metrics
        updated_metrics = await self._recalculate_schedule_metrics(schedule_id, restaurant_id)
        
        # 4. Update the schedule record with new metrics
        update_response = self.supabase.from_('generated_schedules') \
            .update({
                'total_labor_cost': updated_metrics['total_cost'],
                'total_labor_hours': updated_metrics['total_hours'],
                'coverage_score': updated_metrics['coverage_percent'],
                'constraint_violations': updated_metrics['gaps']
            }) \
            .eq('id', schedule_id) \
            .execute()
        
        return {
            "shifts_added": shifts_added,
            "shifts_removed": shifts_removed,
            "updated_metrics": updated_metrics,
            "warnings": warnings
        }

    async def _check_constraints(
        self,
        staff_id: str,
        date: str,
        start_time: str,
        end_time: str,
        restaurant_id: int
    ) -> List[str]:
        """Check if shift violates any constraints (warn but don't block)"""
        warnings = []
        
        # Load constraints for this staff member
        constraints_response = self.supabase.from_('staff_scheduling_rules') \
            .select('*') \
            .eq('staff_id', staff_id) \
            .eq('restaurant_id', restaurant_id) \
            .eq('is_active', True) \
            .execute()
        
        shift_date = datetime.fromisoformat(date).date()
        start_hour = int(start_time.split(':')[0])
        end_hour = int(end_time.split(':')[0])
        
        for constraint in constraints_response.data or []:
            # PTO check
            if constraint['rule_type'] == 'pto':
                pto_start = datetime.fromisoformat(constraint['pto_start_date']).date()
                pto_end = datetime.fromisoformat(constraint['pto_end_date']).date()
                if pto_start <= shift_date <= pto_end:
                    warnings.append(f"⚠️ {constraint.get('description', 'PTO conflict')}")
            
            # Recurring constraints
            elif constraint['rule_type'] == 'recurring':
                recurrence_type = constraint.get('recurrence_type', '')
                
                # Check blocked days
                if recurrence_type == 'cannot_work_specific_days':
                    blocked_days = constraint.get('blocked_days', [])
                    if shift_date.weekday() in blocked_days:
                        warnings.append(f"⚠️ {constraint.get('description', 'Day restriction conflict')}")
                
                # Weekend restrictions
                elif recurrence_type == 'no_weekends' and shift_date.weekday() >= 5:
                    warnings.append(f"⚠️ {constraint.get('description', 'Weekend restriction')}")
                
                elif recurrence_type == 'weekends_only' and shift_date.weekday() < 5:
                    warnings.append(f"⚠️ {constraint.get('description', 'Weekday restriction')}")
                
                # Time restrictions
                elif recurrence_type == 'cannot_work_before_time' and start_hour < 12:
                    warnings.append(f"⚠️ {constraint.get('description', 'Start time restriction')}")
                
                elif recurrence_type == 'cannot_work_after_time' and end_hour >= 22:
                    warnings.append(f"⚠️ {constraint.get('description', 'End time restriction')}")
        
        return warnings

    async def _recalculate_schedule_metrics(self, schedule_id: str, restaurant_id: int) -> Dict:
        """Recalculate coverage, cost, and hours after manual edits"""
        
        # Get all current shifts
        shifts_response = self.supabase.from_('generated_shifts') \
            .select('*, staff(hourly_rate)') \
            .eq('generated_schedule_id', schedule_id) \
            .execute()
        
        shifts = shifts_response.data or []
        
        # Calculate total hours and cost
        total_hours = 0
        total_cost = 0
        
        for shift in shifts:
            start = datetime.strptime(shift['start_time'], '%H:%M:%S')
            end = datetime.strptime(shift['end_time'], '%H:%M:%S')
            hours = (end - start).total_seconds() / 3600
            
            total_hours += hours
            total_cost += hours * float(shift['staff']['hourly_rate'])
        
        # Calculate coverage (compare to demand)
        # Load demand patterns
        demand_response = self.supabase.from_('restaurant_demand_patterns') \
            .select('day_type, hour, covers_per_hour') \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        demand_data = {}
        for row in demand_response.data or []:
            if row['day_type'] not in demand_data:
                demand_data[row['day_type']] = {}
            demand_data[row['day_type']][row['hour']] = row['covers_per_hour']
        
        # Convert covers to staff demand (using same ratios as algorithm)
        total_slots_needed = 0
        filled_slots = 0
        
        for day_type in ['weekday', 'weekend']:
            for hour in range(9, 24):
                covers = demand_data.get(day_type, {}).get(hour, 0)
                if covers == 0:
                    continue
                
                # Calculate staff needed (using algorithm ratios)
                roles = {
                    'Server': max(1, round(covers / 12)),
                    'Cook': max(1, round(covers / 25)),
                    'Host': max(1, round(covers / 30)),
                    'Busser': max(1, round(covers / 25)),
                    'Bartender': max(1, round(covers / 15))
                }
                
                # Count how many slots are filled vs needed
                # This is a simplified calculation - you'd need to match actual shifts to demand
                for role, needed in roles.items():
                    total_slots_needed += needed
                    
                    # Count scheduled staff for this role at this hour
                    # (This is approximate - you'd want to refine based on actual dates)
                    scheduled = len([s for s in shifts if s['position'] == role])
                    filled_slots += min(scheduled, needed)
        
        coverage_percent = (filled_slots / total_slots_needed * 100) if total_slots_needed > 0 else 100
        gaps = max(0, total_slots_needed - filled_slots)
        
        return {
            'total_hours': round(total_hours, 1),
            'total_cost': round(total_cost, 2),
            'coverage_percent': round(coverage_percent, 1),
            'gaps': gaps,
            'total_shifts': len(shifts)
        }
    
    async def approve_schedule(
        self,
        schedule_id: str,
        approved_by: str,
        restaurant_id: int
    ) -> dict:
        """
        Approve a generated schedule and calculate coverage gaps
        
        Flow:
        1. Validate schedule exists and belongs to restaurant
        2. Get all current shifts (after manager edits)
        3. Create approved_schedule record
        4. Copy shifts to approved_shifts
        5. Calculate coverage gaps with priority
        6. Insert gaps into coverage_gaps table
        7. Return summary for Step 5
        """
        
        # 1. Get the generated schedule
        schedule_response = self.supabase.from_('generated_schedules') \
            .select('*') \
            .eq('id', schedule_id) \
            .single() \
            .execute()
        
        if not schedule_response.data:
            raise Exception("Schedule not found")
        
        schedule = schedule_response.data
        
        # Verify ownership
        if schedule['restaurant_id'] != restaurant_id:
            raise Exception("Access denied")
        
        # 2. Get all shifts (includes manager edits)
        shifts_response = self.supabase.from_('generated_shifts') \
            .select('*') \
            .eq('generated_schedule_id', schedule_id) \
            .execute()
        
        shifts = shifts_response.data or []
        
        # Calculate final metrics
        total_cost = sum(
            self._calculate_shift_cost(shift) 
            for shift in shifts
        )
        
        total_hours = sum(
            self._calculate_shift_hours(shift)
            for shift in shifts
        )
        
        # 3. Create approved_schedule
        approved_schedule = {
            'generated_schedule_id': schedule_id,
            'restaurant_id': restaurant_id,
            'scenario_name': schedule.get('scenario_name', 'Generated Schedule'),
            'pay_period_start': schedule['pay_period_start'],
            'pay_period_end': schedule['pay_period_end'],
            'approved_by': approved_by,
            'total_labor_cost': round(total_cost, 2),
            'total_hours': round(total_hours, 2),
            'coverage_score': schedule.get('coverage_score', 0),
            'constraint_violations': schedule.get('constraint_violations', 0)
        }
        
        approved_response = self.supabase.from_('approved_schedules') \
            .insert(approved_schedule) \
            .execute()
        
        if not approved_response.data:
            raise Exception("Failed to create approved schedule")
        
        approved_schedule_id = approved_response.data[0]['id']
        
        # 4. Copy shifts to approved_shifts
        approved_shifts = []
        for shift in shifts:
            approved_shifts.append({
                'approved_schedule_id': approved_schedule_id,
                'restaurant_id': restaurant_id,
                'staff_id': shift['staff_id'],
                'date': shift['date'],
                'start_time': shift['start_time'],
                'end_time': shift['end_time'],
                'position': shift['position']
            })
        
        if approved_shifts:
            self.supabase.from_('approved_shifts').insert(approved_shifts).execute()
        
        # 5. Calculate coverage gaps
        gaps_summary = await self._calculate_coverage_gaps(
            approved_schedule_id=approved_schedule_id,
            restaurant_id=restaurant_id,
            shifts=shifts,
            pay_period_start=schedule['pay_period_start'],
            pay_period_end=schedule['pay_period_end']
        )
        
        return {
            'approved_schedule_id': approved_schedule_id,
            'shifts_count': len(approved_shifts),
            'total_cost': round(total_cost, 2),
            'total_hours': round(total_hours, 2),
            'coverage_score': schedule.get('coverage_score', 0),
            'gaps': gaps_summary
        }


    def _calculate_shift_cost(self, shift: dict) -> float:
        """Calculate cost for a single shift"""
        # Get staff hourly rate
        staff_response = self.supabase.from_('staff') \
            .select('hourly_rate') \
            .eq('staff_id', shift['staff_id']) \
            .single() \
            .execute()
        
        if not staff_response.data:
            return 0
        
        hourly_rate = staff_response.data['hourly_rate']
        
        # Calculate hours
        start_hour = int(shift['start_time'].split(':')[0])
        end_hour = int(shift['end_time'].split(':')[0])
        hours = end_hour - start_hour
        
        return hourly_rate * hours


    def _calculate_shift_hours(self, shift: dict) -> float:
        """Calculate hours for a single shift"""
        start_hour = int(shift['start_time'].split(':')[0])
        end_hour = int(shift['end_time'].split(':')[0])
        return end_hour - start_hour


    async def _calculate_coverage_gaps(
        self,
        approved_schedule_id: str,
        restaurant_id: int,
        shifts: list,
        pay_period_start: str,
        pay_period_end: str
    ) -> dict:
        """
        Calculate coverage gaps with intelligent prioritization
        
        Returns organized summary for Step 5 UI
        """
        from datetime import datetime, timedelta
        
        # Load demand patterns
        demand_response = self.supabase.from_('restaurant_demand_patterns') \
            .select('day_type, hour, covers_per_hour') \
            .eq('restaurant_id', restaurant_id) \
            .execute()
        
        demand_data = {}
        for row in demand_response.data or []:
            if row['day_type'] not in demand_data:
                demand_data[row['day_type']] = {}
            demand_data[row['day_type']][row['hour']] = row['covers_per_hour']
        
        # Load role ratios
        restaurant_response = self.supabase.from_('restaurants') \
            .select('role_ratios') \
            .eq('id', restaurant_id) \
            .single() \
            .execute()
        
        role_ratios = restaurant_response.data.get('role_ratios', {}) if restaurant_response.data else {}
        
        # Organize shifts by day/hour/position
        scheduled = {}  # {date: {position: {hour: count}}}
        for shift in shifts:
            date = shift['date']
            position = shift['position']
            start = int(shift['start_time'].split(':')[0])
            end = int(shift['end_time'].split(':')[0])
            
            if date not in scheduled:
                scheduled[date] = {}
            if position not in scheduled[date]:
                scheduled[date][position] = {}
            
            for hour in range(start, end):
                scheduled[date][position][hour] = scheduled[date][position].get(hour, 0) + 1
        
        # Calculate gaps
        gaps_to_insert = []
        gap_summary = {
            'mission_critical': [],
            'emergency': [],
            'standard': [],
            'total_gaps': 0,
            'total_gap_size': 0
        }
        
        start = datetime.fromisoformat(pay_period_start).date()
        end = datetime.fromisoformat(pay_period_end).date()
        days = (end - start).days + 1
        
        # Peak hours for time criticality
        PEAK_HOURS = [12, 13, 18, 19, 20]
        
        for day_offset in range(days):
            current_date = start + timedelta(days=day_offset)
            date_str = current_date.isoformat()
            day_type = 'weekend' if current_date.weekday() >= 5 else 'weekday'
            day_name = current_date.strftime('%A')
            
            for hour in range(9, 24):
                # Get covers demand
                covers = demand_data.get(day_type, {}).get(hour, 0)
                if covers == 0:
                    continue
                
                # Convert to staff by role
                total_staff = max(1, round(covers / 4))
                
                for position, ratio in role_ratios.items():
                    needed = max(1, round(total_staff * ratio))
                    current = scheduled.get(date_str, {}).get(position, {}).get(hour, 0)
                    gap_size = needed - current
                    
                    if gap_size <= 0:
                        continue
                    
                    # === PRIORITY CALCULATION ===
                    
                    # Determine priority level
                    if gap_size >= 3:
                        priority = 'mission_critical'
                    elif gap_size == 2:
                        priority = 'emergency'
                    else:
                        priority = 'standard'
                    
                    # Calculate time criticality (peak hours)
                    time_criticality = 10 if hour in PEAK_HOURS else 5
                    
                    # Weekend adds criticality
                    if day_type == 'weekend':
                        time_criticality += 3
                    
                    # Friday bonus (weekend prep)
                    if current_date.weekday() == 4:
                        time_criticality += 2
                    
                    gap = {
                        'approved_schedule_id': approved_schedule_id,
                        'restaurant_id': restaurant_id,
                        'date': date_str,
                        'start_time': f'{hour:02d}:00:00',
                        'end_time': f'{hour+1:02d}:00:00',
                        'position': position,
                        'needed_staff': needed,
                        'scheduled_staff': current,
                        'gap_size': gap_size,
                        'priority_level': priority,
                        'demand_score': covers,
                        'time_criticality': time_criticality
                    }
                    
                    gaps_to_insert.append(gap)
                    gap_summary[priority].append({
                        'date': date_str,
                        'day_name': day_name,
                        'hour': hour,
                        'position': position,
                        'gap_size': gap_size,
                        'covers': covers
                    })
                    gap_summary['total_gaps'] += 1
                    gap_summary['total_gap_size'] += gap_size
        
        # Insert gaps into database
        if gaps_to_insert:
            self.supabase.from_('coverage_gaps').insert(gaps_to_insert).execute()
        
        # Add counts to summary
        gap_summary['mission_critical_count'] = len(gap_summary['mission_critical'])
        gap_summary['emergency_count'] = len(gap_summary['emergency'])
        gap_summary['standard_count'] = len(gap_summary['standard'])
        
        return gap_summary