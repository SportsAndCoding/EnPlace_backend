from database.supabase_client import get_supabase
from models.constraints import RecurringConstraintCreate, PTOConstraintCreate
from typing import List, Dict, Optional

class ConstraintsService:
    def __init__(self):
        self.supabase = get_supabase()
    
    async def get_constraints(self, restaurant_id: int) -> List[Dict]:
        """Get all active constraints with staff names"""
        response = self.supabase.from_('staff_scheduling_rules') \
            .select('*, staff(full_name)') \
            .eq('restaurant_id', restaurant_id) \
            .eq('is_active', True) \
            .order('created_at', desc=True) \
            .execute()
        
        constraints = []
        for item in response.data:
            constraint = {**item}
            constraint['staff_name'] = item['staff']['full_name'] if item.get('staff') else 'Unknown'
            del constraint['staff']
            constraints.append(constraint)
        
        return constraints
    
    async def create_recurring_constraint(self, constraint: RecurringConstraintCreate, created_by: str) -> Dict:
        """Create a recurring constraint"""
        data = {
            'restaurant_id': constraint.restaurant_id,
            'staff_id': constraint.staff_id,
            'rule_type': 'recurring',
            'description': constraint.description,
            'recurrence_type': constraint.recurrence_type,
            'recurrence_end_date': str(constraint.recurrence_end_date) if constraint.recurrence_end_date else None,
            'created_by': created_by
        }
        
        response = self.supabase.from_('staff_scheduling_rules') \
            .insert(data) \
            .execute()
        
        if not response.data:
            raise Exception("Failed to create constraint")
        
        return await self.get_constraint_by_id(response.data[0]['id'])
    
    async def create_pto_constraint(self, constraint: PTOConstraintCreate, created_by: str) -> Dict:
        """Create a PTO constraint"""
        data = {
            'restaurant_id': constraint.restaurant_id,
            'staff_id': constraint.staff_id,
            'rule_type': 'pto',
            'description': constraint.description,
            'pto_start_date': str(constraint.pto_start_date),
            'pto_end_date': str(constraint.pto_end_date),
            'pto_reason': constraint.pto_reason,
            'created_by': created_by
        }
        
        response = self.supabase.from_('staff_scheduling_rules') \
            .insert(data) \
            .execute()
        
        if not response.data:
            raise Exception("Failed to create constraint")
        
        return await self.get_constraint_by_id(response.data[0]['id'])
    
    async def get_constraint_by_id(self, constraint_id: str) -> Optional[Dict]:
        """Get a single constraint with staff name"""
        response = self.supabase.from_('staff_scheduling_rules') \
            .select('*, staff(full_name)') \
            .eq('id', constraint_id) \
            .execute()
        
        if not response.data:
            return None
        
        constraint = response.data[0]
        constraint['staff_name'] = constraint['staff']['full_name'] if constraint.get('staff') else 'Unknown'
        del constraint['staff']
        
        return constraint
    
    async def delete_constraint(self, constraint_id: str):
        """Soft delete a constraint"""
        self.supabase.from_('staff_scheduling_rules') \
            .update({'is_active': False}) \
            .eq('id', constraint_id) \
            .execute()