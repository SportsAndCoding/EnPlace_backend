"""
REP SCHEDULING SERVICE
Business logic for sales rep demo scheduling: field hours, availability, appointments
"""
import os
import logging
from datetime import datetime, date, time, timedelta
from typing import Optional, Dict, Any, List
from supabase import create_client, Client

logger = logging.getLogger(__name__)

_supabase_client: Client = None


def get_supabase() -> Client:
    """Get or create Supabase client - lazy initialization"""
    global _supabase_client
    if _supabase_client is None:
        url = os.environ.get('SUPABASE_URL')
        key = os.environ.get('SUPABASE_SERVICE_KEY')
        if not url or not key:
            raise Exception("Supabase credentials not configured")
        _supabase_client = create_client(url, key)
    return _supabase_client


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

DEMO_DURATION_MINUTES = 60  # 1-hour demo blocks
DAYS_OF_WEEK = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

APPOINTMENT_STATUSES = ['scheduled', 'completed', 'cancelled', 'no_show']


class RepSchedulingService:
    """Service class for rep scheduling operations"""

    # ═══════════════════════════════════════════════════════════════════════════
    # REP PUBLIC PROFILE
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_rep_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get rep public info by booking slug"""
        result = get_supabase().table('staff').select(
            'staff_id, full_name, email, phone, position, profile_photo_url, timezone, booking_slug'
        ).eq('booking_slug', slug).eq('portal_access', 'sales_rep').eq('status', 'active').single().execute()
        
        return result.data if result.data else None

    async def get_rep_by_id(self, staff_id: str) -> Optional[Dict[str, Any]]:
        """Get rep info by staff_id"""
        result = get_supabase().table('staff').select(
            'staff_id, full_name, email, phone, position, profile_photo_url, timezone, booking_slug'
        ).eq('staff_id', staff_id).single().execute()
        
        return result.data if result.data else None

    async def update_rep_booking_settings(
        self, 
        staff_id: str, 
        timezone: Optional[str] = None,
        booking_slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update rep's timezone and/or booking slug"""
        updates = {}
        if timezone:
            updates['timezone'] = timezone
        if booking_slug:
            # Validate slug uniqueness
            existing = get_supabase().table('staff').select('staff_id').eq(
                'booking_slug', booking_slug
            ).neq('staff_id', staff_id).execute()
            
            if existing.data:
                raise ValueError(f"Booking slug '{booking_slug}' is already taken")
            
            updates['booking_slug'] = booking_slug.lower().strip()
        
        if not updates:
            raise ValueError("No updates provided")
        
        result = get_supabase().table('staff').update(updates).eq('staff_id', staff_id).execute()
        return result.data[0] if result.data else None

    # ═══════════════════════════════════════════════════════════════════════════
    # FIELD HOURS (Weekly Template)
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_field_hours(self, staff_id: str) -> Dict[str, Any]:
        """
        Get rep's weekly field hours template.
        Returns default template if none exists.
        """
        result = get_supabase().table('rep_field_hours').select('*').eq('staff_id', staff_id).single().execute()
        
        if result.data:
            return result.data
        
        # Return default template (no hours set)
        return {
            'staff_id': staff_id,
            'field_hours': {day: None for day in DAYS_OF_WEEK}
        }

    async def set_field_hours(self, staff_id: str, field_hours: Dict[str, Any]) -> Dict[str, Any]:
        """
        Set rep's weekly field hours template.
        
        field_hours format:
        {
            "monday": {"start": "09:00", "end": "17:00"},
            "tuesday": {"start": "09:00", "end": "17:00"},
            "wednesday": null,  // Not in field
            ...
        }
        """
        # Validate the structure
        for day in DAYS_OF_WEEK:
            if day in field_hours:
                hours = field_hours[day]
                if hours is not None:
                    if not isinstance(hours, dict) or 'start' not in hours or 'end' not in hours:
                        raise ValueError(f"Invalid format for {day}: must have 'start' and 'end' or be null")
                    # Validate time format
                    try:
                        datetime.strptime(hours['start'], '%H:%M')
                        datetime.strptime(hours['end'], '%H:%M')
                    except ValueError:
                        raise ValueError(f"Invalid time format for {day}: use HH:MM")
        
        # Upsert the field hours
        data = {
            'staff_id': staff_id,
            'field_hours': field_hours,
            'updated_at': datetime.utcnow().isoformat()
        }
        
        # Check if exists
        existing = get_supabase().table('rep_field_hours').select('id').eq('staff_id', staff_id).execute()
        
        if existing.data:
            result = get_supabase().table('rep_field_hours').update({
                'field_hours': field_hours,
                'updated_at': datetime.utcnow().isoformat()
            }).eq('staff_id', staff_id).execute()
        else:
            result = get_supabase().table('rep_field_hours').insert(data).execute()
        
        return result.data[0] if result.data else data

    # ═══════════════════════════════════════════════════════════════════════════
    # AVAILABILITY OVERRIDES (Blocked Time)
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_overrides(
        self, 
        staff_id: str, 
        start_date: date, 
        end_date: date
    ) -> List[Dict[str, Any]]:
        """Get availability overrides for a date range"""
        result = get_supabase().table('rep_availability_overrides').select('*').eq(
            'staff_id', staff_id
        ).gte('override_date', start_date.isoformat()).lte('override_date', end_date.isoformat()).execute()
        
        return result.data or []

    async def create_override(
        self,
        staff_id: str,
        override_date: date,
        start_time: Optional[time] = None,
        end_time: Optional[time] = None,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create an availability override (block time).
        If start_time/end_time are None, blocks the entire day.
        """
        data = {
            'staff_id': staff_id,
            'override_date': override_date.isoformat(),
            'start_time': start_time.strftime('%H:%M:%S') if start_time else None,
            'end_time': end_time.strftime('%H:%M:%S') if end_time else None,
            'reason': reason
        }
        
        result = get_supabase().table('rep_availability_overrides').insert(data).execute()
        return result.data[0] if result.data else None

    async def delete_override(self, override_id: int, staff_id: str) -> bool:
        """Delete an override (must belong to the rep)"""
        get_supabase().table('rep_availability_overrides').delete().eq(
            'id', override_id
        ).eq('staff_id', staff_id).execute()
        return True

    # ═══════════════════════════════════════════════════════════════════════════
    # APPOINTMENTS
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_appointments(
        self,
        staff_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get appointments for a rep"""
        query = get_supabase().table('rep_demo_appointments').select('*').eq('staff_id', staff_id)
        
        if start_date:
            query = query.gte('appointment_date', start_date.isoformat())
        if end_date:
            query = query.lte('appointment_date', end_date.isoformat())
        if status:
            query = query.eq('status', status)
        
        query = query.order('appointment_date').order('start_time')
        
        result = query.execute()
        return result.data or []

    async def get_appointment_by_id(self, appointment_id: int) -> Optional[Dict[str, Any]]:
        """Get a single appointment by ID"""
        result = get_supabase().table('rep_demo_appointments').select('*').eq('id', appointment_id).single().execute()
        return result.data if result.data else None

    async def create_appointment(
        self,
        staff_id: str,
        restaurant_name: str,
        contact_name: str,
        appointment_date: date,
        start_time: time,
        contact_email: Optional[str] = None,
        contact_phone: Optional[str] = None,
        location_address: Optional[str] = None,
        location_notes: Optional[str] = None,
        lead_id: Optional[str] = None,
        booked_by: str = 'restaurant',
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new appointment.
        Validates against double-booking via unique constraint.
        """
        end_time_dt = datetime.combine(date.today(), start_time) + timedelta(minutes=DEMO_DURATION_MINUTES)
        end_time = end_time_dt.time()
        
        data = {
            'staff_id': staff_id,
            'restaurant_name': restaurant_name,
            'contact_name': contact_name,
            'contact_email': contact_email,
            'contact_phone': contact_phone,
            'appointment_date': appointment_date.isoformat(),
            'start_time': start_time.strftime('%H:%M:%S'),
            'end_time': end_time.strftime('%H:%M:%S'),
            'location_address': location_address,
            'location_notes': location_notes,
            'lead_id': lead_id,
            'booked_by': booked_by,
            'notes': notes,
            'status': 'scheduled'
        }
        
        try:
            result = get_supabase().table('rep_demo_appointments').insert(data).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                raise ValueError("This time slot is already booked")
            raise

    async def update_appointment(
        self,
        appointment_id: int,
        staff_id: str,
        updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update an appointment (must belong to the rep)"""
        # Filter allowed fields
        allowed = ['restaurant_name', 'contact_name', 'contact_email', 'contact_phone',
                   'location_address', 'location_notes', 'notes', 'status']
        filtered = {k: v for k, v in updates.items() if k in allowed}
        filtered['updated_at'] = datetime.utcnow().isoformat()
        
        result = get_supabase().table('rep_demo_appointments').update(filtered).eq(
            'id', appointment_id
        ).eq('staff_id', staff_id).execute()
        
        return result.data[0] if result.data else None

    async def cancel_appointment(self, appointment_id: int, staff_id: str) -> bool:
        """Cancel an appointment"""
        get_supabase().table('rep_demo_appointments').update({
            'status': 'cancelled',
            'updated_at': datetime.utcnow().isoformat()
        }).eq('id', appointment_id).eq('staff_id', staff_id).execute()
        return True

    # ═══════════════════════════════════════════════════════════════════════════
    # AVAILABILITY COMPUTATION
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_available_slots(
        self,
        staff_id: str,
        target_date: date,
        rep_timezone: str = 'America/New_York'
    ) -> List[Dict[str, Any]]:
        """
        Compute available time slots for a specific date.
        
        Algorithm:
        1. Get field hours for that day of week
        2. If no field hours, return empty
        3. Split into 1-hour blocks
        4. Remove blocks that overlap with overrides
        5. Remove blocks that overlap with existing appointments
        
        Returns list of {"start": "09:00", "end": "10:00", "available": true/false}
        """
        # Get day of week
        day_name = target_date.strftime('%A').lower()
        
        # Get field hours template
        field_hours_data = await self.get_field_hours(staff_id)
        field_hours = field_hours_data.get('field_hours', {})
        
        day_hours = field_hours.get(day_name)
        if not day_hours:
            return []  # Not in field this day
        
        start_str = day_hours['start']
        end_str = day_hours['end']
        
        # Parse times
        start_time = datetime.strptime(start_str, '%H:%M').time()
        end_time = datetime.strptime(end_str, '%H:%M').time()
        
        # Generate all possible slots
        slots = []
        current = datetime.combine(target_date, start_time)
        end_dt = datetime.combine(target_date, end_time)
        
        while current + timedelta(minutes=DEMO_DURATION_MINUTES) <= end_dt:
            slot_end = current + timedelta(minutes=DEMO_DURATION_MINUTES)
            slots.append({
                'start': current.strftime('%H:%M'),
                'end': slot_end.strftime('%H:%M'),
                'available': True
            })
            current = slot_end
        
        if not slots:
            return []
        
        # Get overrides for this date
        overrides = await self.get_overrides(staff_id, target_date, target_date)
        
        # Get appointments for this date
        appointments = await self.get_appointments(staff_id, target_date, target_date, status='scheduled')
        
        # Mark unavailable slots
        for slot in slots:
            slot_start = datetime.strptime(slot['start'], '%H:%M').time()
            slot_end = datetime.strptime(slot['end'], '%H:%M').time()
            
            # Check overrides
            for override in overrides:
                if override['start_time'] is None:
                    # Full day blocked
                    slot['available'] = False
                    slot['reason'] = 'blocked'
                    break
                else:
                    # Partial block - check overlap
                    override_start = datetime.strptime(override['start_time'], '%H:%M:%S').time()
                    override_end = datetime.strptime(override['end_time'], '%H:%M:%S').time()
                    
                    if self._times_overlap(slot_start, slot_end, override_start, override_end):
                        slot['available'] = False
                        slot['reason'] = 'blocked'
                        break
            
            if not slot['available']:
                continue
            
            # Check appointments
            for appt in appointments:
                appt_start = datetime.strptime(appt['start_time'], '%H:%M:%S').time()
                appt_end = datetime.strptime(appt['end_time'], '%H:%M:%S').time()
                
                if self._times_overlap(slot_start, slot_end, appt_start, appt_end):
                    slot['available'] = False
                    slot['reason'] = 'booked'
                    break
        
        return slots

    async def get_available_dates(
        self,
        staff_id: str,
        start_date: date,
        end_date: date
    ) -> List[Dict[str, Any]]:
        """
        Get availability summary for a date range.
        Returns list of {"date": "2026-01-15", "available_slots": 5, "total_slots": 8}
        """
        results = []
        current = start_date
        
        while current <= end_date:
            slots = await self.get_available_slots(staff_id, current)
            available_count = sum(1 for s in slots if s['available'])
            
            results.append({
                'date': current.isoformat(),
                'available_slots': available_count,
                'total_slots': len(slots),
                'has_availability': available_count > 0
            })
            
            current += timedelta(days=1)
        
        return results

    def _times_overlap(
        self,
        start1: time,
        end1: time,
        start2: time,
        end2: time
    ) -> bool:
        """Check if two time ranges overlap"""
        return start1 < end2 and start2 < end1

    # ═══════════════════════════════════════════════════════════════════════════
    # BOOKING (Public - Restaurant Books Demo)
    # ═══════════════════════════════════════════════════════════════════════════

    async def book_demo(
        self,
        slug: str,
        restaurant_name: str,
        contact_name: str,
        contact_email: str,
        contact_phone: str,
        appointment_date: date,
        start_time: time,
        location_address: Optional[str] = None,
        location_notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Public booking endpoint - restaurant books a demo with a rep.
        
        1. Validate rep exists
        2. Validate slot is available
        3. Create appointment
        4. Return confirmation
        """
        # Get rep
        rep = await self.get_rep_by_slug(slug)
        if not rep:
            raise ValueError("Sales representative not found")
        
        staff_id = rep['staff_id']
        
        # Validate date is not in the past
        if appointment_date < date.today():
            raise ValueError("Cannot book appointments in the past")
        
        # Validate slot is available
        slots = await self.get_available_slots(staff_id, appointment_date)
        start_str = start_time.strftime('%H:%M')
        
        matching_slot = next((s for s in slots if s['start'] == start_str), None)
        
        if not matching_slot:
            raise ValueError("This time slot is not available")
        
        if not matching_slot['available']:
            raise ValueError("This time slot is already booked")
        
        # Create appointment
        appointment = await self.create_appointment(
            staff_id=staff_id,
            restaurant_name=restaurant_name,
            contact_name=contact_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
            appointment_date=appointment_date,
            start_time=start_time,
            location_address=location_address,
            location_notes=location_notes,
            booked_by='restaurant'
        )
        
        return {
            'appointment': appointment,
            'rep': {
                'name': rep['full_name'],
                'email': rep['email'],
                'phone': rep['phone']
            }
        }