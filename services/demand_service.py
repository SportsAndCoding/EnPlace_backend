from scipy.interpolate import CubicSpline
from database.supabase_client import get_supabase
from typing import List, Dict

class DemandService:
    def __init__(self):
        self.supabase = get_supabase()
    
    async def get_patterns(self, restaurant_id: int) -> List[Dict]:
        """Get demand patterns for a restaurant"""
        response = self.supabase.from_('restaurant_demand_patterns') \
            .select('day_type, hour, staff_needed') \
            .eq('restaurant_id', restaurant_id) \
            .order('day_type, hour') \
            .execute()
        
        return response.data if response.data else []
    
    def generate_and_save_demand_pattern(
        self,
        restaurant_id: int,
        day_type: str,
        lunch_peak: tuple,
        afternoon_valley: tuple,
        dinner_peak: tuple,
        closing_valley: tuple
    ):
        """Generate smooth demand curve from 4 control points and save to DB"""
        
        # Control points
        control_hours = [9, lunch_peak[0], afternoon_valley[0], dinner_peak[0], closing_valley[0]]
        control_staff = [2, lunch_peak[1], afternoon_valley[1], dinner_peak[1], closing_valley[1]]
        
        # Cubic spline interpolation
        cs = CubicSpline(control_hours, control_staff, bc_type='clamped')
        
        # Generate all hours
        demand_records = []
        for hour in range(9, 24):
            staff_needed = max(2, round(float(cs(hour))))
            demand_records.append({
                'restaurant_id': restaurant_id,
                'day_type': day_type,
                'hour': hour,
                'staff_needed': staff_needed
            })
        
        # Delete old pattern
        self.supabase.from_('restaurant_demand_patterns') \
            .delete() \
            .eq('restaurant_id', restaurant_id) \
            .eq('day_type', day_type) \
            .execute()
        
        # Insert new pattern
        self.supabase.from_('restaurant_demand_patterns') \
            .insert(demand_records) \
            .execute()
        
        return demand_records