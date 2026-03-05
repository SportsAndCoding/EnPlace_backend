"""
SALES SERVICE
Business logic for sales portal: AI parsing, commission calculations, lead management
"""
import os
import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List
from decimal import Decimal
from supabase import create_client, Client
import httpx

logger = logging.getLogger(__name__)

# Initialize Supabase (lazy loaded to ensure env vars are ready)
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

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

LEAD_STAGES = [
    'new',
    'contacted',
    'demo_scheduled',
    'demo_complete',
    'proposal_sent',
    'negotiating',
    'closed_won',
    'closed_lost'
]

ACTIVITY_TYPES = [
    'cold_walk',
    'call',
    'email',
    'demo',
    'meeting',
    'follow_up',
    'note'
]

# Commission rates
COMMISSION_RATES = {
    'first_month': {
        'rep': Decimal('0.75'),
        'captain': Decimal('0.05'),
        'director': Decimal('0.05'),
        'company': Decimal('0.15')
    },
    'residual': {
        'rep': Decimal('0.05'),
        'captain': Decimal('0.05'),
        'director': Decimal('0.05'),
        'company': Decimal('0.85')
    }
}

# Brandon's staff_id (sales director)
SALES_DIRECTOR_ID = 'MAN001'  # Update if different


# ═══════════════════════════════════════════════════════════════════════════════
# AI PARSING
# ═══════════════════════════════════════════════════════════════════════════════

CALL_NOTES_PARSE_PROMPT = """You are an AI assistant that extracts structured data from sales call notes.

Extract the following fields from the notes. If a field cannot be determined, use null.

Required fields (reject if missing):
- restaurant_name: The name of the restaurant
- next_step: What happens next (demo scheduled, follow up call, etc.)

Optional fields:
- contact_name: Name of the person spoken to
- contact_title: Their role (GM, Owner, Manager, etc.)
- contact_email: Email address if mentioned
- contact_phone: Phone number if mentioned
- city_state: Location (city, state)
- staff_count: Number of employees if mentioned
- pain_points: Array of problems they mentioned (turnover, scheduling, etc.)
- objections: Array of concerns or pushback
- outcome: How the conversation went (positive, neutral, negative, no_answer)
- follow_up_date: When to follow up (parse relative dates like "Tuesday" or "next week")
- estimated_monthly_value: Estimated deal size in dollars if determinable

Also generate:
- suggested_stage: Based on the outcome, suggest a pipeline stage
- follow_up_draft: A brief follow-up message to send

Respond ONLY with valid JSON, no markdown or explanation.

Example input:
"Just left Maria at Coastal Grill on Main Street, she's the GM, been there 3 years, 22 staff, turnover is killing them, wants a demo Tuesday 2pm, maria@coastalgrill.com"

Example output:
{
  "restaurant_name": "Coastal Grill",
  "contact_name": "Maria",
  "contact_title": "GM",
  "contact_email": "maria@coastalgrill.com",
  "contact_phone": null,
  "city_state": "Main Street",
  "staff_count": 22,
  "pain_points": ["turnover"],
  "objections": [],
  "outcome": "positive",
  "next_step": "Demo scheduled Tuesday 2pm",
  "follow_up_date": "Tuesday",
  "estimated_monthly_value": 2000,
  "suggested_stage": "demo_scheduled",
  "follow_up_draft": "Maria, great meeting you today! I'll send the calendar invite for Tuesday at 2pm. Looking forward to showing you how En Place can help with the turnover challenges you mentioned."
}
If the notes contain multiple entries, parse ONLY the most recent entry. Always return a single JSON object, never an array.
Now parse these notes:
"""

INCOMPLETE_RESPONSE = {
    "success": False,
    "error": "incomplete",
    "missing_fields": [],
    "message": "This will hurt future-you. Add these details."
}


class SalesService:
    """Service class for sales operations"""
    
    # ═══════════════════════════════════════════════════════════════════════════
    # AI PARSING
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def parse_call_notes(self, notes_text: str) -> Dict[str, Any]:
        """
        Parse call notes using AI and return structured data.
        Does NOT save to database - preview only.
        """
        if not OPENAI_API_KEY:
            raise Exception("OpenAI API key not configured")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": CALL_NOTES_PARSE_PROMPT},
                        {"role": "user", "content": notes_text}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1000
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                raise Exception(f"OpenAI API error: {response.status_code}")
            
            data = response.json()
            content = data['choices'][0]['message']['content']
            
            # Clean up response (remove markdown if present)
            content = content.strip()
            if content.startswith('```'):
                content = content.split('```')[1]
                if content.startswith('json'):
                    content = content[4:]
            content = content.strip()
            
            parsed = json.loads(content)
            
            # Defensive: GPT sometimes returns array for multi-entry notes
            if isinstance(parsed, list):
                parsed = parsed[-1]  # Take the most recent entry
            
            # Validate required fields
            missing = []
            if not parsed.get('restaurant_name'):
                missing.append('restaurant_name')
            if not parsed.get('next_step'):
                missing.append('next_step')
            
            if missing:
                return {
                    "success": False,
                    "error": "incomplete",
                    "missing_fields": missing,
                    "message": "Don't let this lead ghost you. Add these details.",
                    "parsed": parsed,
                    "original_notes": notes_text
                }
            
            return {
                "success": True,
                "parsed": parsed,
                "original_notes": notes_text
            }
    
    async def parse_and_save_call_notes(
        self, 
        notes_text: str, 
        rep_id: str,
        lead_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Parse call notes, create or update lead, and log activity.
        """
        # First parse the notes
        parse_result = await self.parse_call_notes(notes_text)
        
        if not parse_result.get('success'):
            return parse_result
        
        parsed = parse_result['parsed']
        
        # Determine if this is a new lead or existing
        if lead_id:
            # Update existing lead
            lead = await self.update_lead(lead_id, {
                'contact_name': parsed.get('contact_name'),
                'contact_email': parsed.get('contact_email'),
                'contact_phone': parsed.get('contact_phone'),
                'city_state': parsed.get('city_state'),
                'stage': parsed.get('suggested_stage', 'contacted'),
                'estimated_value': parsed.get('estimated_monthly_value'),
                'notes': parsed.get('next_step')
            })
        else:
            # Create new lead
            lead = await self.create_lead({
                'restaurant_name': parsed['restaurant_name'],
                'contact_name': parsed.get('contact_name'),
                'contact_email': parsed.get('contact_email'),
                'contact_phone': parsed.get('contact_phone'),
                'city_state': parsed.get('city_state'),
                'lead_source': 'cold_walk',
                'stage': parsed.get('suggested_stage', 'new'),
                'assigned_rep_id': rep_id,
                'estimated_value': parsed.get('estimated_monthly_value'),
                'notes': parsed.get('next_step')
            })
            lead_id = lead['id']
        
        # Create activity record
        activity_content = f"""Contact: {parsed.get('contact_name', 'Unknown')} ({parsed.get('contact_title', 'Unknown role')})
Staff: {parsed.get('staff_count', 'Unknown')}
Pain Points: {', '.join(parsed.get('pain_points', [])) or 'None noted'}
Objections: {', '.join(parsed.get('objections', [])) or 'None'}
Next Step: {parsed.get('next_step')}

Original Notes: {notes_text}"""

        # Parse follow_up_date
        follow_up = None
        if parsed.get('follow_up_date'):
            follow_up = self._parse_relative_date(parsed['follow_up_date'])
        
        activity = await self.create_activity({
            'lead_id': lead_id,
            'rep_id': rep_id,
            'activity_type': 'cold_walk',
            'content': activity_content,
            'outcome': parsed.get('outcome', 'neutral'),
            'follow_up_date': follow_up.isoformat() if follow_up else None
        })
        
        return {
            "success": True,
            "lead": lead,
            "activity": activity,
            "parsed": parsed,
            "follow_up_draft": parsed.get('follow_up_draft')
        }
    
    def _parse_relative_date(self, date_str: str) -> Optional[date]:
        """Parse relative dates like 'Tuesday' or 'next week' into actual dates"""
        if not date_str:
            return None
            
        date_str = date_str.lower().strip()
        today = date.today()
        
        # Day names
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for i, day in enumerate(days):
            if day in date_str:
                # Find next occurrence of this day
                days_ahead = i - today.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                return today + timedelta(days=days_ahead)
        
        # Relative terms
        if 'tomorrow' in date_str:
            return today + timedelta(days=1)
        if 'next week' in date_str:
            return today + timedelta(days=7)
        if 'end of week' in date_str:
            days_until_friday = 4 - today.weekday()
            if days_until_friday <= 0:
                days_until_friday += 7
            return today + timedelta(days=days_until_friday)
        
        return None
    
    # ═══════════════════════════════════════════════════════════════════════════
    # LEADS CRUD
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def get_leads(
        self, 
        rep_id: str, 
        role: str,
        team_ids: Optional[List[str]] = None,
        stage: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get leads based on role permissions.
        - sales_rep: own leads only
        - sales_captain: own + team leads
        - sales_director: all leads
        """
        query = get_supabase().table('sales_leads').select('*')
        
        if role == 'sales_rep':
            query = query.eq('assigned_rep_id', rep_id)
        elif role == 'sales_captain' and team_ids:
            # Own leads + team leads
            all_ids = [rep_id] + team_ids
            query = query.in_('assigned_rep_id', all_ids)
        # sales_director sees all - no filter
        
        if stage:
            query = query.eq('stage', stage)
        
        query = query.order('updated_at', desc=True).limit(limit)
        
        result = query.execute()
        return result.data or []
    
    async def get_lead_by_id(self, lead_id: str) -> Optional[Dict[str, Any]]:
        """Get single lead with activities"""
        result = get_supabase().table('sales_leads').select('*').eq('id', lead_id).single().execute()
        
        if not result.data:
            return None
        
        lead = result.data
        
        # Get activities for this lead
        activities = get_supabase().table('sales_activities').select('*').eq('lead_id', lead_id).order('created_at', desc=True).execute()
        lead['activities'] = activities.data or []
        
        return lead
    
    async def create_lead(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new lead"""
        result = get_supabase().table('sales_leads').insert(data).execute()
        return result.data[0] if result.data else None
    
    async def update_lead(self, lead_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing lead"""
        # Remove None values
        data = {k: v for k, v in data.items() if v is not None}
        data['updated_at'] = datetime.utcnow().isoformat()
        
        result = get_supabase().table('sales_leads').update(data).eq('id', lead_id).execute()
        return result.data[0] if result.data else None
    
    async def update_lead_stage(self, lead_id: str, new_stage: str) -> Dict[str, Any]:
        """Update lead pipeline stage"""
        if new_stage not in LEAD_STAGES:
            raise ValueError(f"Invalid stage: {new_stage}")
        
        return await self.update_lead(lead_id, {'stage': new_stage})
    
    async def delete_lead(self, lead_id: str) -> bool:
        """Delete a lead and associated activities"""
        # Delete activities first (cascade would handle this but being explicit)
        get_supabase().table('sales_activities').delete().eq('lead_id', lead_id).execute()
        result = get_supabase().table('sales_leads').delete().eq('id', lead_id).execute()
        return True
    
    # ═══════════════════════════════════════════════════════════════════════════
    # ACTIVITIES
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def create_activity(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new activity"""
        result = get_supabase().table('sales_activities').insert(data).execute()
        
        # Also update the lead's updated_at
        if data.get('lead_id'):
            get_supabase().table('sales_leads').update({
                'updated_at': datetime.utcnow().isoformat()
            }).eq('id', data['lead_id']).execute()
        
        return result.data[0] if result.data else None
    
    async def get_activities_for_lead(self, lead_id: str) -> List[Dict[str, Any]]:
        """Get all activities for a lead"""
        result = get_supabase().table('sales_activities').select('*').eq('lead_id', lead_id).order('created_at', desc=True).execute()
        return result.data or []
    
    async def get_rep_activities(
        self, 
        rep_id: str, 
        days: int = 7
    ) -> List[Dict[str, Any]]:
        """Get recent activities for a rep"""
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        result = get_supabase().table('sales_activities').select('*, sales_leads(restaurant_name)').eq('rep_id', rep_id).gte('created_at', since).order('created_at', desc=True).execute()
        return result.data or []
    
    # ═══════════════════════════════════════════════════════════════════════════
    # DEALS & COMMISSIONS
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def create_deal(
        self,
        lead_id: str,
        rep_id: str,
        monthly_value: int,
        contract_months: int = 12,
        captain_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a deal from a closed lead and generate commission records.
        """
        # Create the deal
        deal_data = {
            'lead_id': lead_id,
            'rep_id': rep_id,
            'monthly_value': monthly_value,
            'contract_months': contract_months,
            'closed_at': datetime.utcnow().isoformat(),
            'status': 'active'
        }
        
        deal_result = get_supabase().table('sales_deals').insert(deal_data).execute()
        deal = deal_result.data[0]
        deal_id = deal['id']
        
        # Update lead stage to closed_won
        await self.update_lead_stage(lead_id, 'closed_won')
        
        # Generate commission records
        commissions = []
        
        # First month commission
        first_month_value = Decimal(monthly_value)
        
        # Rep commission (75%)
        rep_commission = float(first_month_value * COMMISSION_RATES['first_month']['rep'])
        commissions.append({
            'deal_id': deal_id,
            'rep_id': rep_id,
            'commission_type': 'first_month',
            'amount': rep_commission,
            'status': 'pending'
        })
        
        # Captain commission (5%) - if exists
        if captain_id:
            captain_commission = float(first_month_value * COMMISSION_RATES['first_month']['captain'])
            commissions.append({
                'deal_id': deal_id,
                'rep_id': captain_id,
                'commission_type': 'first_month_override',
                'amount': captain_commission,
                'status': 'pending'
            })
        
        # Director commission (5%) - always Brandon
        director_commission = float(first_month_value * COMMISSION_RATES['first_month']['director'])
        commissions.append({
            'deal_id': deal_id,
            'rep_id': SALES_DIRECTOR_ID,
            'commission_type': 'first_month_override',
            'amount': director_commission,
            'status': 'pending'
        })
        
        # Insert all commissions
        if commissions:
            get_supabase().table('sales_commissions').insert(commissions).execute()
        
        return {
            'deal': deal,
            'commissions_created': len(commissions)
        }
    
    async def get_deals(
        self,
        rep_id: str,
        role: str,
        team_ids: Optional[List[str]] = None,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get deals based on role permissions"""
        query = get_supabase().table('sales_deals').select('*, sales_leads(restaurant_name)')
        
        if role == 'sales_rep':
            query = query.eq('rep_id', rep_id)
        elif role == 'sales_captain' and team_ids:
            all_ids = [rep_id] + team_ids
            query = query.in_('rep_id', all_ids)
        # sales_director sees all
        
        if status:
            query = query.eq('status', status)
        
        query = query.order('closed_at', desc=True)
        
        result = query.execute()
        return result.data or []
    
    async def get_commissions(
        self,
        rep_id: str,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get commissions for a rep"""
        query = get_supabase().table('sales_commissions').select('*, sales_deals(monthly_value, sales_leads(restaurant_name))').eq('rep_id', rep_id)
        
        if status:
            query = query.eq('status', status)
        
        query = query.order('created_at', desc=True)
        
        result = query.execute()
        return result.data or []
    
    async def get_commission_summary(self, rep_id: str) -> Dict[str, Any]:
        """Get commission summary for a rep (YTD, pending, paid)"""
        all_commissions = await self.get_commissions(rep_id)
        
        # Calculate totals
        year_start = date(date.today().year, 1, 1).isoformat()
        
        ytd_total = Decimal('0')
        pending_total = Decimal('0')
        paid_total = Decimal('0')
        this_month_total = Decimal('0')
        
        month_start = date.today().replace(day=1).isoformat()
        
        for c in all_commissions:
            amount = Decimal(str(c['amount']))
            created = c['created_at'][:10]
            
            if created >= year_start:
                ytd_total += amount
            
            if created >= month_start:
                this_month_total += amount
            
            if c['status'] == 'pending':
                pending_total += amount
            elif c['status'] == 'paid':
                paid_total += amount
        
        return {
            'ytd_earnings': float(ytd_total),
            'this_month': float(this_month_total),
            'pending': float(pending_total),
            'paid': float(paid_total),
            'commission_count': len(all_commissions)
        }
    
    # ═══════════════════════════════════════════════════════════════════════════
    # DASHBOARD & STATS
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def get_dashboard_stats(
        self,
        rep_id: str,
        role: str,
        team_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Get dashboard stats for a rep"""
        
        # Get leads
        leads = await self.get_leads(rep_id, role, team_ids)
        
        # Count by stage
        stage_counts = {stage: 0 for stage in LEAD_STAGES}
        for lead in leads:
            stage = lead.get('stage', 'new')
            if stage in stage_counts:
                stage_counts[stage] += 1
        
        # Get recent activities
        activities = await self.get_rep_activities(rep_id, days=7)
        
        # Get commission summary
        commission_summary = await self.get_commission_summary(rep_id)
        
        # Calculate pipeline value
        pipeline_value = sum(
            lead.get('estimated_value', 0) or 0 
            for lead in leads 
            if lead.get('stage') not in ['closed_won', 'closed_lost']
        )
        
        # Calculate streak (consecutive days with activity)
        streak = self._calculate_streak(activities)
        
        return {
            'leads': {
                'total': len(leads),
                'by_stage': stage_counts,
                'pipeline_value': pipeline_value
            },
            'activities': {
                'this_week': len(activities),
                'streak_days': streak
            },
            'commissions': commission_summary
        }
    
    def _calculate_streak(self, activities: List[Dict[str, Any]]) -> int:
        """Calculate consecutive days with logged activity"""
        if not activities:
            return 0
        
        # Get unique dates with activity
        activity_dates = set()
        for a in activities:
            date_str = a['created_at'][:10]
            activity_dates.add(date_str)
        
        # Count backwards from today
        streak = 0
        check_date = date.today()
        
        while check_date.isoformat() in activity_dates:
            streak += 1
            check_date -= timedelta(days=1)
        
        return streak
    
    async def get_leaderboard(self, metric: str = 'deals', limit: int = 10) -> List[Dict[str, Any]]:
        """Get sales leaderboard"""
        
        if metric == 'deals':
            # Count deals per rep
            result = get_supabase().table('sales_deals').select('rep_id').execute()
            deals = result.data or []
            
            # Count by rep
            counts = {}
            for d in deals:
                rep = d['rep_id']
                counts[rep] = counts.get(rep, 0) + 1
            
            # Sort and get top
            sorted_reps = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
            
            # Get rep names
            leaderboard = []
            for rep_id, count in sorted_reps:
                rep_result = get_supabase().table('staff').select('full_name').eq('staff_id', rep_id).single().execute()
                name = rep_result.data['full_name'] if rep_result.data else rep_id
                leaderboard.append({
                    'rank': len(leaderboard) + 1,
                    'rep_id': rep_id,
                    'name': name,
                    'value': count,
                    'metric': 'deals_closed'
                })
            
            return leaderboard
        
        elif metric == 'revenue':
            # Sum revenue per rep
            result = get_supabase().table('sales_deals').select('rep_id, monthly_value').execute()
            deals = result.data or []
            
            totals = {}
            for d in deals:
                rep = d['rep_id']
                totals[rep] = totals.get(rep, 0) + (d['monthly_value'] or 0)
            
            sorted_reps = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:limit]
            
            leaderboard = []
            for rep_id, total in sorted_reps:
                rep_result = get_supabase().table('staff').select('full_name').eq('staff_id', rep_id).single().execute()
                name = rep_result.data['full_name'] if rep_result.data else rep_id
                leaderboard.append({
                    'rank': len(leaderboard) + 1,
                    'rep_id': rep_id,
                    'name': name,
                    'value': total,
                    'metric': 'monthly_revenue'
                })
            
            return leaderboard
        
        elif metric == 'activities':
            # Count activities in last 7 days
            since = (datetime.utcnow() - timedelta(days=7)).isoformat()
            result = get_supabase().table('sales_activities').select('rep_id').gte('created_at', since).execute()
            activities = result.data or []
            
            counts = {}
            for a in activities:
                rep = a['rep_id']
                counts[rep] = counts.get(rep, 0) + 1
            
            sorted_reps = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
            
            leaderboard = []
            for rep_id, count in sorted_reps:
                rep_result = get_supabase().table('staff').select('full_name').eq('staff_id', rep_id).single().execute()
                name = rep_result.data['full_name'] if rep_result.data else rep_id
                leaderboard.append({
                    'rank': len(leaderboard) + 1,
                    'rep_id': rep_id,
                    'name': name,
                    'value': count,
                    'metric': 'activities_7d'
                })
            
            return leaderboard
        
        return []
    
    # ═══════════════════════════════════════════════════════════════════════════
    # DEMO ACCESS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_demo_credentials(self) -> Dict[str, Any]:
        """Get demo portal credentials for sales reps"""
        return {
            'demo_bistro': {
                'name': 'Demo Bistro',
                'description': 'FULLY LOADED - All modules enabled. Show the full platform.',
                'manager_portal': {
                    'url': 'https://app.en-place.ai/manager-home.html',
                    'email': 'manager@demobistro.com',
                    'password': 'manager123'
                },
                'staff_portal': {
                    'url': 'https://app.en-place.ai/staff-portal/',
                    'email': 'server@demobistro.com',
                    'password': 'server123'
                }
            },
            'baseline_grill': {
                'name': 'Baseline Grill',
                'description': 'CORE ONLY - SSE only. Show paywall UI and upsell opportunities.',
                'manager_portal': {
                    'url': 'https://app.en-place.ai/manager-home.html',
                    'email': 'sarah@baselinegrill.com',
                    'password': 'manager123'
                },
                'staff_portal': {
                    'url': 'https://app.en-place.ai/staff-portal/',
                    'email': 'jake@baselinegrill.com',
                    'password': 'staff123'
                }
            },
            'tips': [
                'Start with Baseline Grill to show core value, then "upgrade" to Demo Bistro',
                'Use the "Billy called in sick" scenario - most compelling demo moment',
                'Show the mood heatmap after explaining daily check-ins',
                'Point out the grayed-out modules: "These unlock when you upgrade"',
                'Demo Bistro: Show House Guardian catching the harassment flag',
                'Demo Bistro: Show SSB preventing burnout scheduling'
            ]
        }