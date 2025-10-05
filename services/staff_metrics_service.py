from datetime import datetime, date
from typing import Dict, Any
from database.supabase_client import get_supabase_client

class StaffMetricsService:
    """
    Service for calculating staff roster metrics and statistics.
    Provides summary data for dashboard cards and reporting.
    """
    
    def __init__(self):
        self.supabase = get_supabase_client()
    
    async def get_staff_metrics(self, restaurant_id: int) -> Dict[str, Any]:
        """
        Calculate key staff metrics for a restaurant.
        
        Args:
            restaurant_id: The restaurant to calculate metrics for
            
        Returns:
            Dictionary containing:
            - total_staff: Total number of employees
            - active_staff: Number of active employees
            - avg_pay_rate: Average hourly rate of active staff
            - new_this_month: Number hired in current month
        """
        try:
            # Fetch all staff for this restaurant
            response = self.supabase.table('staff') \
                .select('staff_id, status, hourly_rate, hire_date') \
                .eq('restaurant_id', restaurant_id) \
                .execute()
            
            if not response.data:
                # No staff yet - return zeros
                return {
                    'success': True,
                    'metrics': {
                        'total_staff': 0,
                        'active_staff': 0,
                        'avg_pay_rate': 0.0,
                        'new_this_month': 0
                    }
                }
            
            staff_list = response.data
            
            # Calculate metrics
            total_staff = len(staff_list)
            
            # Active staff (case-insensitive status check)
            active_staff_list = [s for s in staff_list if s['status'].lower() == 'active']
            active_staff = len(active_staff_list)
            
            # Average pay rate (only active staff)
            if active_staff > 0:
                total_pay = sum(float(s['hourly_rate']) for s in active_staff_list)
                avg_pay_rate = round(total_pay / active_staff, 2)
            else:
                avg_pay_rate = 0.0
            
            # New hires this month
            current_month = date.today().replace(day=1)  # First day of current month
            new_this_month = 0
            
            for staff in staff_list:
                if staff['hire_date']:
                    # Parse hire_date (format: YYYY-MM-DD)
                    hire_date = datetime.strptime(staff['hire_date'], '%Y-%m-%d').date()
                    if hire_date >= current_month:
                        new_this_month += 1
            
            return {
                'success': True,
                'metrics': {
                    'total_staff': total_staff,
                    'active_staff': active_staff,
                    'avg_pay_rate': avg_pay_rate,
                    'new_this_month': new_this_month
                }
            }
            
        except Exception as e:
            print(f"Error calculating staff metrics: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'metrics': {
                    'total_staff': 0,
                    'active_staff': 0,
                    'avg_pay_rate': 0.0,
                    'new_this_month': 0
                }
            }