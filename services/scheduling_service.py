from database.supabase_client import get_supabase
from models.schedule_optimization import ScheduleOptimizer
from typing import List, Dict
from datetime import datetime

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
            "efficiency_percent": result['efficiency_percent'],
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