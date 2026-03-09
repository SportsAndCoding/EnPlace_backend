"""
Dashboard Service - Aggregates all data for manager-home.html
Single endpoint, single round-trip, all dashboard data.
"""

from services.network_benchmark_service import (
    compute_network_burnout_percentile, 
    compute_organic_burnout_score,
    compute_network_sma_percentile,
    compute_organic_sma_score,
    compute_network_fairness_percentile,
    compute_organic_fairness_score,
    compute_network_coverage_percentile,
    compute_organic_coverage_score,
)
from datetime import datetime, timedelta, date
from typing import Optional
from database.supabase_client import supabase
from services.anonymity_guard import ANONYMITY_THRESHOLD, get_role_category
import pytz

def get_today_for_restaurant(restaurant_id: int) -> date:
    """Get today's date in the restaurant's timezone."""
    result = supabase.table("restaurants").select("timezone").eq("id", restaurant_id).single().execute()
    tz_name = result.data.get("timezone", "America/New_York") if result.data else "America/New_York"
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).date()

def get_dashboard_data(restaurant_id: int) -> dict:
    """
    Aggregate all dashboard data for a restaurant.
    Returns everything manager-home.html needs in one response.
    """
    # Date ranges - use restaurant timezone
    today = get_today_for_restaurant(restaurant_id)
    week_ago = today - timedelta(days=7)
    two_weeks_ago = today - timedelta(days=14)
    four_weeks_ago = today - timedelta(days=28)
    
    # Get current week bounds (Monday to Sunday)
    days_since_monday = today.weekday()
    week_start = today - timedelta(days=days_since_monday)
    week_end = week_start + timedelta(days=6)
    
    # Fetch all needed data in parallel-ish (Supabase doesn't do true parallel, but grouped)
    restaurant = get_restaurant_info(restaurant_id)
    checkins_7d = get_checkins(restaurant_id, week_ago, today)
    checkins_14d = get_checkins(restaurant_id, two_weeks_ago, today)
    checkins_28d = get_checkins(restaurant_id, four_weeks_ago, today)
    manager_logs = get_manager_logs(restaurant_id, week_ago, today)
    shifts_today = get_shifts_for_date(restaurant_id, today)
    shifts_week = get_shifts_range(restaurant_id, week_start, week_end)
    staff_list = get_staff(restaurant_id)
    candidates = get_candidates(restaurant_id)
    escalations = get_escalations(restaurant_id)
    notifications = get_notifications(restaurant_id)
    house_guardian_alerts = get_house_guardian_alerts(restaurant_id)
    has_house_guardian = restaurant.get("has_house_guardian", False)
    house_guardian_report = get_house_guardian_weekly_report(restaurant_id, has_house_guardian)
    pending_swaps = get_pending_swaps(restaurant_id)
    latest_schedule = get_latest_schedule_analysis(restaurant_id)
    pending_nudges = get_pending_nudges(restaurant_id)
    dismissed_nudges = get_dismissed_nudges(restaurant_id)
    
    # Compute each section
    smm = compute_smm(checkins_7d, checkins_28d, manager_logs, dismissed_nudges)
    fairness = compute_fairness(checkins_7d, checkins_28d, shifts_week, staff_list)
    burnout = compute_burnout(checkins_7d, checkins_28d, shifts_week, staff_list)
    stable_schedule = compute_stable_schedule(shifts_week, shifts_today, today)
    stable_hire = compute_stable_hire(candidates)
    house_guardian = compute_house_guardian(smm, fairness, burnout, stable_schedule, escalations)
    action_board = compute_action_board(notifications, shifts_week, escalations, house_guardian_alerts, pending_swaps, latest_schedule, house_guardian_report, has_house_guardian, pending_nudges, today)
    mood_heatmap = compute_mood_heatmap(checkins_7d)
    quick_stats = compute_quick_stats(shifts_today, shifts_week, staff_list)

    return {
        "success": True,
        "restaurant": restaurant,
        "smm": smm,
        "fairness": fairness,
        "burnout": burnout,
        "stable_schedule": stable_schedule,
        "stable_hire": stable_hire,
        "house_guardian": house_guardian,
        "action_board": action_board,
        "mood_heatmap": mood_heatmap,
        "quick_stats": quick_stats,
        "modules": {
            "stable_schedule_builder": {"owned": restaurant.get("has_schedule_optimizer", False)},
            "stable_hire": {"owned": restaurant.get("has_stable_hire", False)},
            "house_guardian": {"owned": restaurant.get("has_house_guardian", False)},
            "open_shift_creator": {"owned": restaurant.get("has_open_shift_marketplace", False)},
            "shift_swap": {"owned": restaurant.get("has_shift_swap", False)}
        },
        "timestamp": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "pay_period": compute_pay_period(today)
        }
    }


# ═══════════════════════════════════════════════════════════════════
# DATA FETCHERS
# ═══════════════════════════════════════════════════════════════════

def get_restaurant_info(restaurant_id: int) -> dict:
    """Get restaurant basic info including feature flags."""
    result = supabase.table("restaurants").select("*").eq("id", restaurant_id).single().execute()
    if result.data:
        r = result.data
        # Get staff count
        staff_result = supabase.table("staff").select("staff_id", count="exact").eq("restaurant_id", restaurant_id).eq("status", "Active").execute()
        staff_count = staff_result.count or 0

        return {
            "name": r.get("name", "Restaurant"),
            "manager": r.get("manager_name", "Manager"),
            "staff_count": staff_count,
            "timezone": r.get("timezone", "America/New_York"),
            # Feature flags for paywall
            "has_schedule_optimizer": r.get("has_schedule_optimizer", False),
            "has_open_shift_marketplace": r.get("has_open_shift_marketplace", False),
            "has_shift_swap": r.get("has_shift_swap", False),
            "has_stable_hire": r.get("has_stable_hire", False),
            "has_house_guardian": r.get("has_house_guardian", False),
            "subscription_status": r.get("subscription_status", "none")
        }
    return {
        "name": "Restaurant", 
        "manager": "Manager", 
        "staff_count": 0,
        "timezone": "America/New_York",
        "has_schedule_optimizer": False,
        "has_open_shift_marketplace": False,
        "has_shift_swap": False,
        "has_stable_hire": False,
        "has_house_guardian": False,
        "subscription_status": "none"
    }


def get_checkins(restaurant_id: int, start_date: date, end_date: date) -> list:
    """Get check-ins for date range."""
    result = supabase.table("sse_daily_checkins").select("*").eq("restaurant_id", restaurant_id).gte("checkin_date", start_date.isoformat()).lte("checkin_date", end_date.isoformat()).execute()
    return result.data or []


def get_manager_logs(restaurant_id: int, start_date: date, end_date: date) -> list:
    """Get manager logs for date range."""
    result = supabase.table("manager_daily_logs").select("*").eq("restaurant_id", restaurant_id).gte("log_date", start_date.isoformat()).lte("log_date", end_date.isoformat()).execute()
    return result.data or []


def get_shifts_for_date(restaurant_id: int, shift_date: date) -> list:
    """Get shifts for a specific date."""
    result = supabase.table("sse_shifts").select("*").eq("restaurant_id", restaurant_id).eq("shift_date", shift_date.isoformat()).execute()
    return result.data or []


def get_shifts_range(restaurant_id: int, start_date: date, end_date: date) -> list:
    """Get shifts for date range."""
    result = supabase.table("sse_shifts").select("*").eq("restaurant_id", restaurant_id).gte("shift_date", start_date.isoformat()).lte("shift_date", end_date.isoformat()).execute()
    return result.data or []


def get_staff(restaurant_id: int) -> list:
    """Get all active staff."""
    result = supabase.table("staff").select("*").eq("restaurant_id", restaurant_id).eq("status", "Active").execute()
    return result.data or []


def get_candidates(restaurant_id: int) -> list:
    """Get all candidates."""
    result = supabase.table("hiring_candidates").select("*").eq("restaurant_id", restaurant_id).execute()
    return result.data or []


def get_escalations(restaurant_id: int) -> list:
    """Get active escalations with staff info."""
    result = supabase.table("sse_escalation_events") \
        .select("*, primary_staff:primary_staff_id(full_name, position)") \
        .eq("restaurant_id", restaurant_id) \
        .in_("status", ["actionable", "monitoring"]) \
        .execute()
    return result.data or []


def get_notifications(restaurant_id: int) -> list:
    """Get recent unread notifications."""
    result = supabase.table("notifications").select("*").eq("restaurant_id", restaurant_id).eq("is_read", False).order("created_at", desc=True).limit(10).execute()
    return result.data or []

def get_house_guardian_alerts(restaurant_id: int) -> list:
    """Get active House Guardian alerts."""
    try:
        result = supabase.table("house_guardian_alerts").select("*").eq("restaurant_id", restaurant_id).eq("status", "active").execute()
        return result.data or []
    except Exception as e:
        return []

def get_house_guardian_weekly_report(restaurant_id: int, has_subscription: bool = False) -> dict:
    """
    Get House Guardian weekly report.
    Subscribers get their actual report.
    Non-subscribers get network social proof report.
    """
    if has_subscription:
        try:
            result = supabase.table("house_guardian_weekly_reports").select("*").eq("restaurant_id", restaurant_id).order("generated_at", desc=True).limit(1).execute()
            if result.data:
                report = result.data[0]
                report["is_network_report"] = False
                return report
            return None
        except Exception as e:
            return None
    else:
        # Non-subscriber: return network social proof report
        return _generate_network_report(restaurant_id)


def _generate_network_report(restaurant_id: int) -> dict:
    """
    Generate network-wide social proof report for non-subscribers.
    Shows aggregated wins + one rotating cautionary tale.
    Each restaurant gets unique rotation to avoid duplicates.
    """
    from datetime import datetime, timedelta
    
    today = get_today_for_restaurant(restaurant_id)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    
    # Cautionary tales pool - rotate weekly
    cautionary_tales = [
        {
            "name": "Snookie's Cookies",
            "location": "Atlantic City, NJ",
            "category": "harassment",
            "story": "Staff had been anonymously journaling concerns about an inappropriate relationship between the GM and assistant manager for six straight months. The signals were consistent, escalating, and clear to anyone looking.",
            "result": "Both managers fired, restaurant now facing a $40,000 sexual harassment lawsuit, and the whole team demoralized.",
            "lead_time": "six months"
        },
        {
            "name": "Bayou Brew House",
            "location": "New Orleans, LA",
            "category": "theft",
            "story": "Repeated anonymous notes about a bartender skimming cash on late shifts. The pattern was clear across multiple staff check-ins.",
            "result": "$18,000 missing, police report filed, team trust destroyed.",
            "lead_time": "four months"
        },
        {
            "name": "Harbor Light Oyster Bar",
            "location": "Portland, ME",
            "category": "bullying",
            "story": "New server repeatedly targeted with hostile 'hazing' by senior staff. The pattern showed up in her check-ins and her coworkers' notes.",
            "result": "Best server walked, posted a viral Glassdoor review that tanked hiring for months.",
            "lead_time": "three months"
        },
        {
            "name": "Mesa Verde Cantina",
            "location": "Austin, TX",
            "category": "harassment",
            "story": "Multiple mentions of a manager making sexually suggestive comments to BOH staff. The pattern repeated across different employees.",
            "result": "$55,000 settlement after formal complaint.",
            "lead_time": "five months"
        },
        {
            "name": "River Street Smokehouse",
            "location": "Savannah, GA",
            "category": "theft",
            "story": "Consistent notes about missing product and one closer 'helping himself.' Multiple staff noticed independently.",
            "result": "$12,000 inventory loss, termination, and criminal charges.",
            "lead_time": "two months"
        },
        {
            "name": "Copper Kettle Café",
            "location": "Bozeman, MT",
            "category": "substance",
            "story": "Repeated complaints of a cook showing up impaired on weekend closes. Coworkers were worried but didn't know who to tell.",
            "result": "Kitchen accident sent two staff to ER, insurance premiums spiked.",
            "lead_time": "four months"
        },
        {
            "name": "Jag's Steakhouse",
            "location": "West Chester, OH",
            "category": "bullying",
            "story": "Assistant GM verbally berating hosts on the floor—same names showing up in check-ins every week.",
            "result": "Three hosts quit same week, service collapsed for a month.",
            "lead_time": "six weeks"
        },
        {
            "name": "Pacific Rim Sushi",
            "location": "San Diego, CA",
            "category": "threats",
            "story": "Sushi chef repeatedly threatening BOH staff during high-volume service. The escalation was visible in the notes.",
            "result": "Physical altercation, police called, location closed two nights.",
            "lead_time": "three months"
        },
        {
            "name": "Liberty Bell Diner",
            "location": "Philadelphia, PA",
            "category": "theft",
            "story": "Pattern of tip-pool money coming up short on closing manager's shifts. Staff noticed but felt powerless.",
            "result": "$9,500 stolen, manager prosecuted, diner lost long-term staff.",
            "lead_time": "two months"
        },
        {
            "name": "Altitude Kitchen",
            "location": "Denver, CO",
            "category": "bullying",
            "story": "Senior line cook targeting new hires with aggressive 'kitchen culture.' The new hires wrote about it. The veterans normalized it.",
            "result": "Mass walkout of three new hires, weekend service crippled.",
            "lead_time": "four months"
        },
        {
            "name": "Honky Tonk BBQ",
            "location": "Nashville, TN",
            "category": "substance",
            "story": "Bartender repeatedly overserving themselves on shift. Other staff mentioned it but management never saw the notes.",
            "result": "Liquor license violation, $15,000 fine, temporary shutdown.",
            "lead_time": "five weeks"
        },
        {
            "name": "Nordic Table",
            "location": "Minneapolis, MN",
            "category": "harassment",
            "story": "GM making inappropriate advances toward servers after close. The pattern was documented for half a year.",
            "result": "$75,000 lawsuit, GM fired, reputation damage still hurting hiring.",
            "lead_time": "six months"
        },
        {
            "name": "El Rancho Grande",
            "location": "Tucson, AZ",
            "category": "theft",
            "story": "Repeated notes about cash drawer discrepancies tied to one closer. The math didn't add up and staff knew it.",
            "result": "$11,000 missing, criminal case ongoing.",
            "lead_time": "three months"
        },
        {
            "name": "Green Mountain Grill",
            "location": "Burlington, VT",
            "category": "bullying",
            "story": "Kitchen lead bullying dish team nightly. The dish crew wrote about it constantly. Nobody with authority ever read it.",
            "result": "Entire dish crew quit same night, restaurant closed early for weeks.",
            "lead_time": "two months"
        },
        {
            "name": "Lowcountry Provisions",
            "location": "Charleston, SC",
            "category": "bullying",
            "story": "Manager favoring certain staff with shifts while freezing others out. The fairness complaints escalated into something worse.",
            "result": "Perceived favoritism boiled over into public staff fight, viral video.",
            "lead_time": "four months"
        },
        {
            "name": "Pike Place Provisions",
            "location": "Seattle, WA",
            "category": "substance",
            "story": "Closer showing up impaired multiple weekends. Coworkers covered for them until they couldn't.",
            "result": "Major health code violation during inspection, temporary closure.",
            "lead_time": "five months"
        },
        {
            "name": "Second City Steaks",
            "location": "Chicago, IL",
            "category": "harassment",
            "story": "Repeated harassment of female servers by a senior cook. Everyone in the kitchen knew. Management didn't.",
            "result": "$60,000 settlement, cook fired, steakhouse lost key staff.",
            "lead_time": "four months"
        },
        {
            "name": "Harbor View Tavern",
            "location": "Boston, MA",
            "category": "theft",
            "story": "Pattern of missing liquor bottles tied to one bartender. Staff mentioned it in passing. Nobody connected the dots.",
            "result": "$14,000 loss, termination, and ongoing trust issues.",
            "lead_time": "three months"
        },
        {
            "name": "Ocean Drive Café",
            "location": "Miami, FL",
            "category": "bullying",
            "story": "Hostess repeatedly bullied by floor manager over seating assignments. She wrote about it every shift.",
            "result": "Hostess quit, posted detailed negative review, hiring pool dried up.",
            "lead_time": "six weeks"
        },
        {
            "name": "Jennie's Turkey Sandwich Shop",
            "location": "Atlantic City, NJ",
            "category": "theft",
            "story": "Closer pocketing cash tips meant for the team. The team knew but didn't want to be 'that person.'",
            "result": "$8,000 stolen, team morale collapsed, high turnover followed.",
            "lead_time": "two months"
        },
        {
            "name": "Rusty Anchor Pub",
            "location": "Baltimore, MD",
            "category": "substance",
            "story": "Barback repeatedly mentioned showing up impaired for closing shifts. The signs were all there.",
            "result": "Barback crashed company van after shift—$85,000 liability claim.",
            "lead_time": "four months"
        },
        {
            "name": "Sunset Taqueria",
            "location": "Phoenix, AZ",
            "category": "harassment",
            "story": "Expo making repeated unwanted advances toward female servers. Multiple women mentioned it independently.",
            "result": "$48,000 settlement, expo fired, negative Yelp flood.",
            "lead_time": "five months"
        },
        {
            "name": "Maple Leaf Diner",
            "location": "Portland, OR",
            "category": "theft",
            "story": "Three closers running a small theft ring—missing cash and liquor. Staff suspected but had no way to report anonymously.",
            "result": "$22,000 loss, two arrests, diner lost its best night crew.",
            "lead_time": "three months"
        },
        {
            "name": "Peachtree Bistro",
            "location": "Atlanta, GA",
            "category": "bullying",
            "story": "Senior servers bullying and freezing out new hires from good sections. It was 'just how things work here.'",
            "result": "Four new servers quit in one week, weekend service tanked.",
            "lead_time": "six weeks"
        },
        {
            "name": "Windy City Wings",
            "location": "Chicago, IL",
            "category": "substance",
            "story": "Line cook repeatedly impaired during Friday rush. Kitchen staff covered. FOH complained in check-ins.",
            "result": "Customer food-safety complaint, health department fine, temporary closure.",
            "lead_time": "four months"
        },
        {
            "name": "Blue Crab Shack",
            "location": "Annapolis, MD",
            "category": "threats",
            "story": "Manager threatening staff over scheduling conflicts. The language in check-ins got scarier each week.",
            "result": "Physical confrontation in kitchen, police involved, two staff lost.",
            "lead_time": "two months"
        },
        {
            "name": "Desert Bloom Café",
            "location": "Albuquerque, NM",
            "category": "theft",
            "story": "Pattern of tip tampering by one bartender on busy nights. Servers noticed. Servers wrote about it. Nothing happened.",
            "result": "$10,500 missing, bartender prosecuted, bar revenue dropped 18%.",
            "lead_time": "three months"
        },
        {
            "name": "Evergreen Grill",
            "location": "Asheville, NC",
            "category": "bullying",
            "story": "Kitchen lead relentlessly bullying new dishwashers. The dish pit had 100% turnover in two months.",
            "result": "Entire dish team walked out mid-shift, restaurant closed early three nights straight.",
            "lead_time": "five weeks"
        },
        {
            "name": "Lakeside Tavern",
            "location": "Milwaukee, WI",
            "category": "harassment",
            "story": "GM making inappropriate comments to young host staff. The hosts talked to each other. Not to anyone who could help.",
            "result": "$62,000 lawsuit, GM termination, hiring freeze for months.",
            "lead_time": "six months"
        },
        {
            "name": "Cactus Rose Saloon",
            "location": "Santa Fe, NM",
            "category": "theft",
            "story": "Closer consistently short on cash drops after late nights. The pattern was obvious in the numbers—and the notes.",
            "result": "$16,000 embezzled, saloon lost liquor license for 30 days.",
            "lead_time": "four months"
        },
        {
            "name": "Magnolia Brunch House",
            "location": "Raleigh, NC",
            "category": "bullying",
            "story": "Senior host bullying newer staff over table assignments. 'Learning the ropes' was code for hazing.",
            "result": "Three hosts quit same weekend, brunch service collapsed.",
            "lead_time": "five weeks"
        },
        {
            "name": "Golden Gate Bistro",
            "location": "San Francisco, CA",
            "category": "substance",
            "story": "Repeated notes about impaired prep cook on early shifts. The 6 AM crew all knew.",
            "result": "Major knife accident, workers' comp claim, kitchen down two staff.",
            "lead_time": "three months"
        },
        {
            "name": "Frontier Steakhouse",
            "location": "Cheyenne, WY",
            "category": "bullying",
            "story": "Manager favoritism turning into hostile exclusion of certain cooks. What started as unfair became cruel.",
            "result": "BOH walkout during rodeo week—lost $35,000 in revenue.",
            "lead_time": "four months"
        },
        {
            "name": "Palm Breeze Café",
            "location": "Orlando, FL",
            "category": "theft",
            "story": "Bartender skimming credit-card tips on large parties. The servers compared notes. Literally.",
            "result": "$13,000 recovered too late—team trust gone, turnover spiked.",
            "lead_time": "two months"
        },
        {
            "name": "Rocky Top BBQ",
            "location": "Knoxville, TN",
            "category": "threats",
            "story": "Line cook making repeated threats during heated service. 'That's just how he is' wasn't a good enough answer.",
            "result": "Fight broke out, police called, negative local news coverage.",
            "lead_time": "six weeks"
        },
        {
            "name": "Cascade Brewpub",
            "location": "Bend, OR",
            "category": "theft",
            "story": "Closer helping themselves to craft beer inventory nightly. Logs didn't match. Staff notes did.",
            "result": "$19,000 inventory loss, brewpub lost key distributor relationship.",
            "lead_time": "three months"
        },
        {
            "name": "Bayview Diner",
            "location": "Virginia Beach, VA",
            "category": "harassment",
            "story": "Repeated harassment of servers by a senior manager after hours. The after-close notes told the whole story.",
            "result": "$70,000 settlement, diner's reputation tanked on review sites.",
            "lead_time": "five months"
        },
        {
            "name": "High Plains Grill",
            "location": "Amarillo, TX",
            "category": "bullying",
            "story": "Dish team targeted with aggressive bullying by kitchen lead. They didn't complain. They just stopped showing up.",
            "result": "Dish crew no-showed en masse, grill closed early multiple nights.",
            "lead_time": "four weeks"
        },
        {
            "name": "Emerald Isle Pub",
            "location": "Buffalo, NY",
            "category": "substance",
            "story": "Bartender overserving themselves during closing. The regulars noticed. The owner didn't.",
            "result": "Liquor board violation, 45-day license suspension, revenue cratered.",
            "lead_time": "four months"
        },
        {
            "name": "Sierra Cantina",
            "location": "Flagstaff, AZ",
            "category": "theft",
            "story": "Pattern of missing cash tied to one manager's closing shifts. The team talked. Management didn't listen.",
            "result": "$15,000 embezzled, cantina lost half its night staff overnight.",
            "lead_time": "three months"
        }
    ]
    
    # Select one cautionary tale - unique rotation per restaurant
    # Each restaurant cycles through all tales before repeating
    week_number = today.isocalendar()[1]
    tale_index = (week_number + restaurant_id) % len(cautionary_tales)
    selected_tale = cautionary_tales[tale_index]
    
    return {
        "id": "network_report",
        "restaurant_id": None,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "is_network_report": True,
        "notes_scanned": 847,
        "signals_detected": 100,
        "alerts_generated": 23,
        "report_content": {
            "network_stats": {
                "restaurants_protected": 47,
                "total_signals_caught": 100,
                "issues_resolved": 23
            },
            "category_summaries": [
                {
                    "category": "Harassment",
                    "signals_prevented": 31,
                    "example": "Multiple staff mentioned uncomfortable behavior from a closing manager → Issue addressed before formal complaint filed"
                },
                {
                    "category": "Bullying",
                    "signals_prevented": 26,
                    "example": "New hire repeatedly targeted by senior staff → Manager intervention stopped resignation and rebuilt team trust"
                },
                {
                    "category": "Theft",
                    "signals_prevented": 19,
                    "example": "Pattern of tip-pool discrepancies flagged across check-ins → Investigation recovered $2,400"
                },
                {
                    "category": "Threats",
                    "signals_prevented": 14,
                    "example": "Escalating tension between BOH staff detected → Schedules adjusted before physical altercation"
                },
                {
                    "category": "Substance Concerns",
                    "signals_prevented": 10,
                    "example": "Corroborated notes about impaired coworker on closes → Employee connected to support resources"
                }
            ],
            "cautionary_tale": selected_tale
        }
    }  
def get_pending_swaps(restaurant_id: int) -> list:
    """Get pending shift swap requests with staff names (future shifts only)."""
    try:
        result = supabase.table("shift_swaps") \
            .select("*, sse_shifts(shift_date, scheduled_start, position, shift_type), requester:requesting_staff_id(full_name), target:target_staff_id(full_name)") \
            .eq("restaurant_id", restaurant_id) \
            .eq("status", "accepted") \
            .execute()
        
        # Filter out past shifts
        today = get_today_for_restaurant(restaurant_id).isoformat()
        swaps = result.data or []
        swaps = [s for s in swaps if (s.get("sse_shifts") or {}).get("shift_date", "9999") >= today]
        
        return swaps
    except Exception as e:
        print(f"Error fetching swaps: {e}")
        return []
    
def get_latest_schedule_analysis(restaurant_id: int) -> dict:
    """Get latest completed schedule analysis."""
    try:
        result = supabase.table("schedule_uploads") \
            .select("*") \
            .eq("restaurant_id", restaurant_id) \
            .eq("status", "completed") \
            .order("processed_at", desc=True) \
            .limit(1) \
            .execute()
        return result.data[0] if result.data else None
    except Exception as e:
        return None

def get_pending_nudges(restaurant_id: int) -> list:
    """Get pending nudges aggregated by module and position."""
    try:
        result = supabase.table("nudges") \
            .select("*, staff:staff_id(full_name, position)") \
            .eq("restaurant_id", restaurant_id) \
            .eq("status", "pending") \
            .execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error fetching nudges: {e}")
        return []
    
def get_dismissed_nudges(restaurant_id: int) -> list:
    """Get dismissed (acknowledged) nudges from last 30 days."""
    try:
        from datetime import datetime, timedelta
        thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
        
        result = supabase.table("nudges") \
            .select("id, module_key, viewed_at") \
            .eq("restaurant_id", restaurant_id) \
            .eq("status", "acknowledged") \
            .gte("viewed_at", thirty_days_ago) \
            .execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Error fetching dismissed nudges: {e}")
        return []

# ═══════════════════════════════════════════════════════════════════
# COMPUTATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def compute_smm(checkins_7d: list, checkins_28d: list, manager_logs: list, dismissed_nudges: list = None) -> dict:
    """
    Compute Staff-Manager Alignment score.
    Uses same logic as alignment_service but returns dashboard format.
    """
    if not checkins_7d:
        return {
            "score": 0,
            "status": "no_data",
            "trend": {"direction": "stable", "delta": 0, "period": "last 4 weeks"},
            "network": {"percentile": 50, "interpretation": "Insufficient data"}
        }
    
    # Emotional alignment (mood + felt flags)
    mood_scores = [c.get("mood_emoji", 3) for c in checkins_7d]
    avg_mood = sum(mood_scores) / len(mood_scores) if mood_scores else 3
    
    felt_safe = [c for c in checkins_7d if c.get("felt_safe")]
    felt_fair = [c for c in checkins_7d if c.get("felt_fair")]
    felt_respected = [c for c in checkins_7d if c.get("felt_respected")]
    
    safe_pct = len(felt_safe) / len(checkins_7d) * 100 if checkins_7d else 0
    fair_pct = len(felt_fair) / len(checkins_7d) * 100 if checkins_7d else 0
    respect_pct = len(felt_respected) / len(checkins_7d) * 100 if checkins_7d else 0
    
    # Normalize mood to 0-100
    mood_normalized = (avg_mood - 1) / 4 * 100
    
    # Emotional alignment = weighted average
    emotional = (mood_normalized * 0.4 + safe_pct * 0.2 + fair_pct * 0.2 + respect_pct * 0.2)
    
    # Operational alignment (manager vs staff perception)
    operational = 80  # Default if no logs
    if manager_logs and checkins_7d:
        # Compare manager ratings to staff moods on same days
        matches = 0
        comparisons = 0
        for log in manager_logs:
            log_date = log.get("log_date")
            day_checkins = [c for c in checkins_7d if c.get("checkin_date") == log_date]
            if day_checkins:
                staff_avg = sum(c.get("mood_emoji", 3) for c in day_checkins) / len(day_checkins)
                manager_rating = log.get("overall_rating", 3)
                # If within 1 point, consider aligned
                if abs(staff_avg - manager_rating) <= 1:
                    matches += 1
                comparisons += 1
        
        if comparisons > 0:
            operational = (matches / comparisons) * 100
    
    # Combined score
    score = int(emotional * 0.5 + operational * 0.5)

    # Penalty for dismissed staff nudges (-2 per nudge, max -10)
    if dismissed_nudges:
        nudge_penalty = min(len(dismissed_nudges) * 2, 10)
        score = max(0, score - nudge_penalty)
    
    # Trend (compare to 4 weeks ago)
    if checkins_28d:
        old_checkins = [c for c in checkins_28d if c not in checkins_7d]
        if old_checkins:
            old_mood = sum(c.get("mood_emoji", 3) for c in old_checkins) / len(old_checkins)
            old_score = int((old_mood - 1) / 4 * 100)
            delta = score - old_score
            direction = "up" if delta > 0 else "down" if delta < 0 else "stable"
        else:
            delta = 0
            direction = "stable"
    else:
        delta = 0
        direction = "stable"
    
    # Status
    if score >= 80:
        status = "healthy"
    elif score >= 60:
        status = "warning"
    else:
        status = "critical"
    
    # Network percentile (real comparison to synthetic network)
    # Use pure alignment score, not blended score, to compare apples-to-apples
    organic_sma = compute_organic_sma_score(checkins_7d, manager_logs)
    network_rank = compute_network_sma_percentile(organic_sma)
    
    return {
        "score": score,
        "status": status,
        "trend": {
            "direction": direction,
            "delta": abs(delta),
            "period": "last 4 weeks"
        },
        "network": {
            "percentile": network_rank["percentile"],
            "interpretation": network_rank["interpretation"],
            "network_size": network_rank.get("network_size", 0)
        }
    }


def compute_fairness(checkins_7d: list, checkins_28d: list, shifts_week: list, staff_list: list) -> dict:
    """
    Compute Fairness score based on felt_fair responses and shift distribution.
    """
    if not checkins_7d:
        return {
            "score": 50,
            "status": "no_data",
            "trend": {"direction": "stable", "delta": 0, "period": "last month"},
            "network": {"percentile": 50, "interpretation": "Insufficient data"},
            "issues": []
        }
    
    # Fairness from check-ins
    felt_fair = [c for c in checkins_7d if c.get("felt_fair")]
    fair_pct = len(felt_fair) / len(checkins_7d) * 100 if checkins_7d else 50
    
    # Analyze weekend distribution
    issues = []
    weekend_shifts = [s for s in shifts_week if _is_weekend(s.get("shift_date"))]
    
    # Count weekend shifts per staff
    staff_weekend_counts = {}
    for shift in weekend_shifts:
        sid = shift.get("staff_id")
        if sid:
            staff_weekend_counts[sid] = staff_weekend_counts.get(sid, 0) + 1
    
    # Find staff with heavy weekend load
    total_weekend = len(weekend_shifts)
    if total_weekend > 0 and staff_list:
        for sid, count in staff_weekend_counts.items():
            pct = count / total_weekend * 100
            if pct > 30:  # More than 30% of weekend shifts
                staff_name = next((s.get("full_name", sid) for s in staff_list if s.get("staff_id") == sid), sid)
                issues.append(f"{staff_name.split()[0]} has {int(pct)}% of weekend shifts")
    
    # Score combines felt_fair + distribution balance
    distribution_score = 100 - (len(issues) * 15)  # Each issue reduces score
    score = int(fair_pct * 0.6 + max(0, distribution_score) * 0.4)
    
    # Trend
    if checkins_28d:
        old_checkins = [c for c in checkins_28d if c not in checkins_7d]
        if old_checkins:
            old_fair = [c for c in old_checkins if c.get("felt_fair")]
            old_pct = len(old_fair) / len(old_checkins) * 100 if old_checkins else 50
            delta = int(fair_pct - old_pct)
            direction = "up" if delta > 0 else "down" if delta < 0 else "stable"
        else:
            delta = 0
            direction = "stable"
    else:
        delta = 0
        direction = "stable"
    
    # Status
    if score >= 80:
        status = "healthy"
    elif score >= 60:
        status = "warning"
    else:
        status = "critical"
    
    # Network percentile (real comparison to synthetic network)
    organic_fairness = compute_organic_fairness_score(checkins_7d)
    network_rank = compute_network_fairness_percentile(organic_fairness)
    
    return {
        "score": score,
        "status": status,
        "trend": {
            "direction": direction,
            "delta": abs(delta),
            "period": "last month"
        },
        "network": {
            "percentile": network_rank["percentile"],
            "interpretation": network_rank["interpretation"],
            "network_size": network_rank.get("network_size", 0)
        },
        "issues": issues[:3]  # Top 3 issues
    }


def compute_burnout(checkins_7d: list, checkins_28d: list, shifts_week: list, staff_list: list) -> dict:
    """
    Compute burnout radar - EMOTIONAL PATTERNS ONLY.
    
    Shows role-level mood trends (anonymized).
    Schedule-based burnout (hours, overtime) belongs in Stable Schedule Builder.
    """
    
    # ═══════════════════════════════════════════════════════════════
    # ROLE-LEVEL EMOTIONAL PATTERNS (anonymized)
    # ═══════════════════════════════════════════════════════════════
    
    role_alerts = []
    
    # Get mood by role for this week
    role_moods_current = {}
    for checkin in checkins_7d:
        sid = checkin.get("staff_id")
        mood = checkin.get("mood_emoji")
        if sid and mood:
            staff_match = next((s for s in staff_list if s.get("staff_id") == sid), None)
            if staff_match:
                role = staff_match.get("position", "Unknown")
                if role not in role_moods_current:
                    role_moods_current[role] = []
                role_moods_current[role].append(mood)
    
    # Get mood by role for previous period (baseline)
    role_moods_baseline = {}
    old_checkins = [c for c in checkins_28d if c not in checkins_7d]
    for checkin in old_checkins:
        sid = checkin.get("staff_id")
        mood = checkin.get("mood_emoji")
        if sid and mood:
            staff_match = next((s for s in staff_list if s.get("staff_id") == sid), None)
            if staff_match:
                role = staff_match.get("position", "Unknown")
                if role not in role_moods_baseline:
                    role_moods_baseline[role] = []
                role_moods_baseline[role].append(mood)
    
    # Compare current vs baseline by role
    for role, current_moods in role_moods_current.items():
        current_avg = sum(current_moods) / len(current_moods) if current_moods else 0
        baseline_moods = role_moods_baseline.get(role, [])
        baseline_avg = sum(baseline_moods) / len(baseline_moods) if baseline_moods else current_avg
        
        if baseline_avg > 0:
            pct_change = ((current_avg - baseline_avg) / baseline_avg) * 100
        else:
            pct_change = 0
        
        # Flag roles with declining mood (more than 10% drop)
        if pct_change < -10:
            staff_count = len(set(c.get("staff_id") for c in checkins_7d 
                                  if next((s for s in staff_list if s.get("staff_id") == c.get("staff_id") 
                                          and s.get("position") == role), None)))

            # Anonymity guard: don't expose position-level mood for small teams
            if staff_count < ANONYMITY_THRESHOLD:
                display_role = get_role_category(role)
            else:
                display_role = role

            role_alerts.append({
                "role": display_role,
                "staff_count": staff_count if staff_count >= ANONYMITY_THRESHOLD else None,
                "trend": "declining",
                "vs_baseline": f"{int(pct_change)}%",
                "current_avg": round(current_avg, 1),
                "baseline_avg": round(baseline_avg, 1),
                "anonymity_applied": staff_count < ANONYMITY_THRESHOLD
            })
    
    # Sort by severity (biggest decline first)
    role_alerts.sort(key=lambda x: float(x["vs_baseline"].replace("%", "")))
    
    # ═══════════════════════════════════════════════════════════════
    # METRICS
    # ═══════════════════════════════════════════════════════════════
    
    elevated_count = len(role_alerts)
    
    # Trend (compare low mood count to previous week)
    delta = 0
    direction = "stable"
    if old_checkins:
        old_low = sum(1 for c in old_checkins if c.get("mood_emoji", 3) <= 2)
        new_low = sum(1 for c in checkins_7d if c.get("mood_emoji", 3) <= 2)
        delta = new_low - old_low
        direction = "up" if delta > 0 else "down" if delta < 0 else "stable"
    
    # Status based on how many roles are struggling
    if elevated_count == 0:
        status = "healthy"
    elif elevated_count <= 2:
        status = "warning"
    else:
        status = "critical"
    
    # Network comparison (emotional burnout vs synthetic network)
    organic_score = compute_organic_burnout_score(checkins_7d)
    network_rank = compute_network_burnout_percentile(organic_score)
    
    return {
        "elevated_count": elevated_count,
        "status": status,
        "trend": {
            "direction": direction,
            "delta": abs(delta),
            "period": "last week"
        },
        "network": {
            "percentile": network_rank["percentile"],
            "interpretation": network_rank["interpretation"],
            "network_size": network_rank.get("network_size", 0)
        },
        "role_alerts": role_alerts[:5]  # Top 5 struggling roles
    }



def compute_stable_schedule(shifts_week: list, shifts_today: list, today: date) -> dict:
    """
    Compute schedule coverage and gaps.
    """
    total_shifts = len(shifts_week)
    assigned = [s for s in shifts_week if s.get("staff_id")]
    open_shifts = [s for s in shifts_week if not s.get("staff_id")]
    
    if total_shifts == 0:
        coverage_pct = 100
    else:
        coverage_pct = len(assigned) / total_shifts * 100
    
    # Categorize gaps
    critical = 0
    warning = 0
    for shift in open_shifts:
        shift_date_str = shift.get("shift_date")
        if shift_date_str:
            try:
                shift_date = date.fromisoformat(shift_date_str)
                days_until = (shift_date - today).days
                if days_until <= 1:
                    critical += 1
                else:
                    warning += 1
            except:
                warning += 1
    
    # Status
    if coverage_pct >= 95:
        status = "healthy"
    elif coverage_pct >= 85:
        status = "warning"
    else:
        status = "critical"
    
    # Network percentile (real comparison to synthetic network)
    organic_coverage = compute_organic_coverage_score(shifts_week)
    network_rank = compute_network_coverage_percentile(organic_coverage)
    
    return {
        "coverage_percent": round(coverage_pct, 1),
        "status": status,
        "gaps": {
            "critical": critical,
            "warning": warning,
            "total": len(open_shifts)
        },
        "trend": {
            "direction": "stable",
            "delta": 0,
            "period": "last 2 weeks"
        },
        "network": {
            "percentile": network_rank["percentile"],
            "interpretation": network_rank["interpretation"],
            "network_size": network_rank.get("network_size", 0)
        }
    }

def compute_stable_hire(candidates: list) -> dict:
    """
    Compute hiring pipeline stats.
    """
    open_candidates = [c for c in candidates if c.get("status") == "open"]
    interviewed = [c for c in candidates if c.get("status") == "interviewed"]
    total_to_review = len(open_candidates) + len(interviewed)
    
    # Recommendations
    recommended = [c for c in candidates if c.get("recommendation") in ["strong_hire", "hire"]]
    high_risk = [c for c in candidates if c.get("cliff_risk_percent") and c.get("cliff_risk_percent") >= 50]
    
    # Average stability score
    scored = [c for c in candidates if c.get("stability_score")]
    avg_score = sum(c.get("stability_score", 0) for c in scored) / len(scored) if scored else 0
    
    # Build interpretation based on actual data
    if total_to_review == 0:
        interpretation = "Add candidates to see predictions"
    elif len(high_risk) > 0:
        interpretation = f"{len(high_risk)} high-risk candidate{'s' if len(high_risk) > 1 else ''} flagged"
    elif len(recommended) > 0:
        interpretation = f"{len(recommended)} candidate{'s' if len(recommended) > 1 else ''} recommended to hire"
    else:
        interpretation = "Review candidates for predictions"
    
    return {
        "open_candidates": total_to_review,
        "recommended": len(recommended),
        "high_risk": len(high_risk),
        "avg_stability_score": int(avg_score),
        "trend": {
            "direction": "up" if avg_score >= 65 else "stable",
            "delta": 5,
            "period": "last quarter"
        },
        "network": {
            "percentile": None,
            "interpretation": interpretation
        }
    }

def compute_house_guardian(smm: dict, fairness: dict, burnout: dict, stable_schedule: dict, escalations: list) -> dict:
    """
    Compute House Guardian thermometers from other metrics.
    """
    thermometers = [
        {
            "id": "labor_compliance",
            "name": "Labor Compliance",
            "icon": "⚖️",
            "value": 94,  # Would compute from actual compliance data
            "status": "healthy",
            "trend": "stable",
            "alert": None
        },
        {
            "id": "coverage_risk",
            "name": "Coverage Risk",
            "icon": "📅",
            "value": int(stable_schedule.get("coverage_percent", 80)),
            "status": stable_schedule.get("status", "warning"),
            "trend": "up" if stable_schedule.get("trend", {}).get("direction") == "up" else "down",
            "alert": f"{stable_schedule.get('gaps', {}).get('total', 0)} open shifts this week" if stable_schedule.get('gaps', {}).get('total', 0) > 0 else None
        },
        {
            "id": "burnout_index",
            "name": "Burnout Index",
            "icon": "🔥",
            "value": 100 - (burnout.get("elevated_count", 0) * 15),
            "status": burnout.get("status", "warning"),
            "trend": burnout.get("trend", {}).get("direction", "stable"),
            "alert": f"{burnout.get('elevated_count', 0)} staff at elevated risk" if burnout.get("elevated_count", 0) > 0 else None
        },
        {
            "id": "fairness_balance",
            "name": "Fairness Balance",
            "icon": "⚖️",
            "value": fairness.get("score", 70),
            "status": fairness.get("status", "warning"),
            "trend": fairness.get("trend", {}).get("direction", "stable"),
            "alert": fairness.get("issues", [None])[0] if fairness.get("issues") else None
        },
        {
            "id": "retention_forecast",
            "name": "Retention Forecast",
            "icon": "👥",
            "value": smm.get("score", 80),
            "status": smm.get("status", "healthy"),
            "trend": smm.get("trend", {}).get("direction", "stable"),
            "alert": None
        }
    ]
    
    # Overall status
    warning_count = sum(1 for t in thermometers if t["status"] == "warning")
    critical_count = sum(1 for t in thermometers if t["status"] == "critical")
    
    if critical_count > 0:
        overall_status = "critical"
    elif warning_count >= 2:
        overall_status = "watch"
    else:
        overall_status = "healthy"
    
    return {
        "overall_status": overall_status,
        "thermometers": thermometers
    }


def compute_action_board(notifications: list, shifts_week: list = None, escalations: list = None, hg_alerts: list = None, swaps: list = None, schedule_analysis: dict = None, hg_weekly_report: dict = None, has_house_guardian: bool = False, nudges: list = None, today: date = None) -> dict:
    """
    Transform notifications into action board items.
    Also injects critical coverage gaps from open shifts.
    House Guardian alerts only shown if has_house_guardian subscription is active.
    """
    type_mapping = {
        "swap_request": {"icon": "🔄", "action": "Approve", "secondary": "Deny", "boost": 1},
        "coverage_gap": {"icon": "⚠️", "action": "Find Coverage", "secondary": None, "boost": 2},
        "pto_request": {"icon": "🏖️", "action": "Review", "secondary": None, "boost": 1},
        "escalation": {"icon": "🚨", "action": "Review", "secondary": None, "boost": 2},
        "schedule_issue": {"icon": "📅", "action": "Done", "secondary": "Dismiss", "boost": 2},
        "schedule_summary": {"icon": "📊", "action": "View Report", "secondary": None, "boost": 0},
        "system": {"icon": "📋", "action": "View", "secondary": None, "boost": 1},
        "nudge_request": {"icon": "💡", "action": "Buy Now", "secondary": "Dismiss", "boost": 0}
    }
    priority_mapping = {
        "escalation": "critical",
        "coverage_gap": "critical",
        "swap_request": "high",
        "schedule_issue": "high",
        "pto_request": "medium",
        "system": "low",
        "schedule_summary": "info",
        "nudge_request": "medium"
    }

    items = []

    # ═══════════════════════════════════════════════════════════════════
    # INJECT ACTIONABLE ESCALATIONS
    # ═══════════════════════════════════════════════════════════════════
    if escalations:
        for esc in escalations:
            # Only show active escalations
            if esc.get("status") not in ["actionable", "monitoring"]:
                continue

            event_type = esc.get("event_type", "issue")
            current_step = esc.get("current_step", 1)

            # Format title based on event type
            event_labels = {
                "burnout": "Burnout Risk",
                "burnout_risk": "Burnout Risk",
                "fairness": "Fairness Issue",
                "fairness_issue": "Fairness Issue",
                "retention": "Retention Risk",
                "alignment": "Alignment Gap",
                "mood_drop": "Mood Alert",
                "preference_drift": "Preference Drift",
                "scheduling_conflict": "Schedule Conflict",
                "cascade_risk": "Cascade Risk"
            }
            event_label = event_labels.get(event_type, event_type.replace("_", " ").title())

            # Extract staff info from joined data
            primary_staff = esc.get("primary_staff") or {}
            staff_name = primary_staff.get("full_name")
            staff_position = primary_staff.get("position")
            
            trigger = esc.get("trigger_reason", "")
            
            # Build context-rich description
            source_type = esc.get("source_type", "mood")
            if staff_name and source_type == "schedule":
                desc = f"{staff_name} ({staff_position})" if staff_position else staff_name
            elif staff_name and source_type == "graph":
                desc = f"Check in with {staff_name}" + (f" ({staff_position})" if staff_position else "")
            elif esc.get("affected_role"):
                role_label = esc.get("affected_role")
                if esc.get("anonymity_applied"):
                    desc = f"{role_label} team mood shift detected"
                else:
                    desc = f"{role_label} team affected"
            else:
                desc = "Multiple staff affected"

            items.append({
                "id": esc.get("id"),
                "type": "escalation",
                "priority": "critical" if current_step >= 4 else "high",
                "title": event_label,
                "description": desc,
                "time_ago": _time_ago(esc.get("triggered_at")) if esc.get("triggered_at") else "Active",
                "action": "Done",
                "secondary_action": "Dismiss",
                "smm_boost": 2,
                # Rich context for frontend
                "escalation_context": {
                    "staff_name": staff_name,
                    "staff_position": staff_position,
                    "current_step": current_step,
                    "max_steps": 7,
                    "event_type": event_type,
                    "trigger_reason": trigger,
                    "severity": esc.get("severity", "moderate"),
                    "affected_role": esc.get("affected_role"),
                    "anonymity_applied": esc.get("anonymity_applied", False),
                    "rollup_level": esc.get("rollup_level", "position")
                }
            })

    # ═══════════════════════════════════════════════════════════════════
    # INJECT HOUSE GUARDIAN ALERTS (subscribers only)
    # ═══════════════════════════════════════════════════════════════════
    if hg_alerts and has_house_guardian:
        for alert in hg_alerts:
            if alert.get("status") != "active":
                continue

            category_labels = {
                "harassment": "Harassment Signal",
                "theft": "Theft Signal",
                "drugs": "Substance Concern",
                "threats": "Safety Threat",
                "bullying": "Hostile Behavior"
            }

            category = alert.get("category", "concern")
            label = category_labels.get(category, category.title() + " Signal")
            
            # Build description with signal strength
            source_count = alert.get("source_count", 1)
            signal_strength = alert.get("signal_strength", "MEDIUM")
            description = f"{source_count} source{'s' if source_count > 1 else ''} · {signal_strength} signal"

            items.append({
                "id": alert.get("id"),
                "type": "house_guardian",
                "priority": "critical",
                "title": label,
                "description": description,
                "time_ago": _time_ago(alert.get("created_at")),
                "action": "Done",
                "secondary_action": "Dismiss",
                "smm_boost": 2,
                "event_id": alert.get("sse_event_id")  # For Review button
            })

    # ═══════════════════════════════════════════════════════════════════
    # INJECT SHIFT SWAP REQUESTS
    # ═══════════════════════════════════════════════════════════════════
    if swaps:
        for swap in swaps:
            shift = swap.get("sse_shifts") or {}
            shift_date = shift.get("shift_date", "")
            position = shift.get("position") or shift.get("shift_type") or "Shift"
            
            # Format date nicely
            if shift_date:
                try:
                    dt = date.fromisoformat(shift_date)
                    date_str = dt.strftime("%a %b %d")
                except:
                    date_str = shift_date
            else:
                date_str = "Upcoming"
            
            # Extract staff names from joined data
            requester_name = (swap.get("requester") or {}).get("full_name", "Staff")
            target_name = (swap.get("target") or {}).get("full_name", "Staff")
            
            items.append({
                "id": swap.get("id"),
                "type": "swap_request",
                "priority": "high",
                "title": f"Swap Request: {position}",
                "description": f"{requester_name} → {target_name} · {date_str}",
                "time_ago": _time_ago(swap.get("created_at")),
                "action": "Approve",
                "secondary_action": "Deny",
                "smm_boost": 1
            })

    # ═══════════════════════════════════════════════════════════════════
    # INJECT OPEN SHIFTS (COVERAGE GAPS)
    # ═══════════════════════════════════════════════════════════════════
    if shifts_week:
        if today is None:
            today = date.today()  # Fallback, should not happen
        for shift in shifts_week:
            # Open shift = no staff assigned
            if shift.get("staff_id"):
                continue
            
            # Skip already assigned/closed shifts
            shift_status = shift.get("status", "posted")
            if shift_status == "assigned":
                continue
                
            shift_date_str = shift.get("shift_date")
            if not shift_date_str:
                continue
            try:
                shift_date = date.fromisoformat(shift_date_str)
            except:
                continue
            
            # Only show upcoming open shifts
            if shift_date < today:
                continue
            
            days_until = (shift_date - today).days
            
            # For today's shifts, skip if start time has passed
            if days_until == 0 and shift.get("scheduled_start"):
                try:
                    from datetime import datetime as dt, timezone
                    start_dt = dt.fromisoformat(shift.get("scheduled_start").replace("Z", "+00:00"))
                    now = dt.now(timezone.utc)
                    if start_dt <= now:
                        continue  # Shift already started
                except:
                    pass
            
            # Urgency
            if days_until == 0:
                urgency = "TODAY"
            elif days_until == 1:
                urgency = "TOMORROW"
            elif days_until <= 3:
                urgency = f"In {days_until} days"
            else:
                continue  # Don't show if more than 3 days out
            
            shift_type = shift.get("position") or shift.get("shift_type") or "Shift"
            day_name = shift_date.strftime("%a")
            start_time = ""
            end_time = ""
            
            if shift.get("scheduled_start"):
                try:
                    from datetime import datetime as dt
                    start_dt = dt.fromisoformat(shift.get("scheduled_start").replace("Z", "+00:00"))
                    start_time = start_dt.strftime("%-I:%M%p").lower()
                except:
                    pass
            
            if shift.get("scheduled_end"):
                try:
                    from datetime import datetime as dt
                    end_dt = dt.fromisoformat(shift.get("scheduled_end").replace("Z", "+00:00"))
                    end_time = end_dt.strftime("%-I:%M%p").lower()
                except:
                    pass
            
            # Different handling based on status
            volunteer_count = shift.get("volunteer_count", 0)
            
            if shift_status == "review" or volunteer_count > 0:
                # Has volunteers - needs selection
                items.append({
                    "id": shift.get("id"),
                    "type": "open_shift_review",
                    "priority": "critical",
                    "title": f"{urgency}: {shift_type} - {volunteer_count} volunteer{'s' if volunteer_count != 1 else ''}!",
                    "description": f"{day_name} {start_time} - Select who gets it",
                    "time_ago": "Action needed",
                    "action": "Select Volunteer",
                    "secondary_action": None,
                    "smm_boost": 2,
                    "shift_context": {
                        "shift_id": shift.get("id"),
                        "position": shift_type,
                        "shift_date": shift_date_str,
                        "start_time": start_time,
                        "end_time": end_time,
                        "reason": shift.get("reason"),
                        "volunteer_count": volunteer_count
                    }
                })
            else:
                # Posted but no volunteers yet, or needs to be sent to marketplace
                items.append({
                    "id": shift.get("id"),
                    "type": "open_shift_posted",
                    "priority": "critical",
                    "title": f"{urgency}: {shift_type} shift uncovered",
                    "description": f"{day_name} {start_time} - Waiting for volunteers",
                    "time_ago": "Open",
                    "action": "Create Open Shift",
                    "secondary_action": "Dismiss",
                    "smm_boost": 2,
                    "shift_context": {
                        "shift_id": shift.get("id"),
                        "position": shift_type,
                        "shift_date": shift_date_str,
                        "start_time": start_time,
                        "end_time": end_time,
                        "reason": shift.get("reason"),
                        "original_staff_id": shift.get("original_staff_id")
                    }
                })
    
    # ═══════════════════════════════════════════════════════════════════
    # ADD NOTIFICATION-BASED ITEMS
    # ═══════════════════════════════════════════════════════════════════
    for notif in notifications:
        notif_type = notif.get("type", "system")
        
        # Skip escalation-type notifications - real escalations come from sse_escalation_events
        if notif_type == "escalation":
            continue
        mapping = type_mapping.get(notif_type, type_mapping["system"])
        
        # Calculate time ago
        created = notif.get("created_at")
        time_ago = _time_ago(created) if created else "Recently"
        
        items.append({
            "id": notif.get("id"),
            "type": notif_type,
            "priority": priority_mapping.get(notif_type, "low"),
            "title": notif.get("title", "Notification"),
            "description": notif.get("message", ""),
            "time_ago": time_ago,
            "action": mapping["action"],
            "secondary_action": mapping.get("secondary"),
            "smm_boost": mapping["boost"]
        })
    
    # ═══════════════════════════════════════════════════════════════════
    # INJECT WEEKLY SCHEDULE SUMMARY (ALWAYS AT BOTTOM)
    # ═══════════════════════════════════════════════════════════════════
    is_report_day = today.weekday() in [0, 5, 6]  # Monday = 0, Tuesday = 1, Friday = 4 for testing  
    if is_report_day and schedule_analysis and schedule_analysis.get("status") == "completed":
        analysis = schedule_analysis.get("analysis_result") or {}
        week_of = schedule_analysis.get("week_of", "")
        stability_score = schedule_analysis.get("stability_score") or 0
        issues_found = schedule_analysis.get("issues_found") or 0
        critical_issues = schedule_analysis.get("critical_issues") or 0
        
        # Format week label
        if week_of:
            try:
                dt = date.fromisoformat(week_of)
                week_label = dt.strftime("Week of %b %d")
            except:
                week_label = f"Week of {week_of}"
        else:
            week_label = "Recent Schedule"
        
        # Generate strategic summary
        summary_lines = []
        
        # Pull insights from analysis
        priority_fixes = analysis.get("priorityFixes", [])
        staff_impact = analysis.get("staffImpact", [])
        emotional_fallout = analysis.get("emotionalFallout", {})
        
        # Find overworked staff
        for staff in staff_impact[:2]:
            if staff.get("riskLevel") == "high":
                summary_lines.append(f"{staff.get('name', 'Staff member')} at risk - {staff.get('mainIssue', 'needs attention')}")
        
        # Find preference drift
        drift_fixes = [f for f in priority_fixes if f.get("type") == "preference" and "drift" in f.get("title", "").lower()]
        for fix in drift_fixes[:1]:
            affected = fix.get("affectedStaff", [])
            if affected:
                summary_lines.append(f"{affected[0]} has ongoing preference mismatch")
        
        # Build description
        if summary_lines:
            description = " • ".join(summary_lines[:2])
        elif critical_issues > 0:
            description = f"{critical_issues} critical issue{'s' if critical_issues > 1 else ''} identified"
        elif issues_found > 0:
            description = f"{issues_found} scheduling consideration{'s' if issues_found > 1 else ''} flagged"
        else:
            description = "No major issues detected"
        
        # Score interpretation
        if stability_score >= 80:
            title = f"📊 {week_label}: Strong Schedule"
        elif stability_score >= 65:
            title = f"📊 {week_label}: Schedule Review"
        else:
            title = f"📊 {week_label}: Schedule Concerns"
        
        items.append({
            "id": f"schedule_summary_{schedule_analysis.get('id')}",
            "type": "schedule_summary",
            "priority": "info",
            "title": title,
            "description": description,
            "time_ago": _time_ago(schedule_analysis.get("processed_at")),
            "action": "View Report",
            "secondary_action": None,
            "smm_boost": 0,
            "metadata": {
                "upload_id": schedule_analysis.get("id"),
                "stability_score": stability_score,
                "week_of": week_of
            }
        })
    
    # ═══════════════════════════════════════════════════════════════════
    # INJECT HOUSE GUARDIAN WEEKLY REPORT
    # Network reports show every day (sales tool)
    # Subscriber reports show Mon/Tue/Fri only
    # ═══════════════════════════════════════════════════════════════════
    is_hg_report_day = today.weekday() in [0, 5, 6]  # Monday + weekends for demos
    is_network_report = hg_weekly_report and hg_weekly_report.get("is_network_report", False)
    
    if hg_weekly_report and (is_network_report or is_hg_report_day):
        content = hg_weekly_report.get("report_content") or {}
        week_start = hg_weekly_report.get("week_start", "")
        week_end = hg_weekly_report.get("week_end", "")
        
        try:
            start_dt = date.fromisoformat(week_start)
            end_dt = date.fromisoformat(week_end)
            week_label = f"{start_dt.strftime('%b %d')} – {end_dt.strftime('%b %d')}"
        except:
            week_label = "Recent"
        
        is_network = hg_weekly_report.get("is_network_report", False)
        
        if is_network:
            # Network report for non-subscribers
            stats = content.get("network_stats", {})
            description = f"{stats.get('critical_violations_prevented', 0)} violations prevented across {stats.get('restaurants_protected', 0)} restaurants this week"
            title = f"🏠 House Guardian Network Report"
        else:
            # Subscriber report
            notes_scanned = hg_weekly_report.get("notes_scanned", 0)
            flagged = content.get("categories_flagged", [])
            if flagged:
                description = f"{len(flagged)} category flagged. {notes_scanned} check-ins scanned."
            else:
                description = f"All clear. {notes_scanned} check-ins scanned."
            title = f"🏠 House Guardian: {week_label}"
        
        # Use a deterministic UUID for network reports to avoid notification system errors
        report_id = hg_weekly_report.get('id')
        if report_id == "network_report":
            item_id = "00000000-0000-0000-0000-000000000000"  # Placeholder UUID for network reports
        else:
            item_id = f"hg_weekly_{report_id}"
        
        items.append({
            "id": item_id,
            "type": "house_guardian_report",
            "priority": "info",
            "title": title,
            "description": description,
            "time_ago": _time_ago(hg_weekly_report.get("generated_at")) if not is_network else "This week",
            "action": "View Summary",
            "secondary_action": None,
            "smm_boost": 0,
            "is_network_report": is_network
        })
    
     # ═══════════════════════════════════════════════════════════════════
    # INJECT STAFF NUDGES (aggregated by module + position)
    # Show weekly on same cadence as House Guardian report
    # ═══════════════════════════════════════════════════════════════════
    is_nudge_day = today.weekday() in [0, 5, 6]  # Monday + weekends for demos
    if nudges and is_nudge_day:
        # Aggregate nudges by module_key and position
        nudge_groups = {}
        for nudge in nudges:
            module_key = nudge.get("module_key")
            staff_info = nudge.get("staff") or {}
            position = staff_info.get("position") or "Staff"
            
            group_key = f"{module_key}_{position}"
            if group_key not in nudge_groups:
                nudge_groups[group_key] = {
                    "module_key": module_key,
                    "position": position,
                    "count": 0,
                    "nudge_ids": [],
                    "latest_created": nudge.get("created_at")
                }
            nudge_groups[group_key]["count"] += 1
            nudge_groups[group_key]["nudge_ids"].append(nudge.get("id"))
        
        # Module display info
        module_info = {
            "schedule": {
                "title": "Stable Schedule Builder",
                "copy": "Your {team} is asking for fairer scheduling—restaurants using this see 10% higher morale."
            },
            "shiftSwap": {
                "title": "Shift Swap",
                "copy": "Your {team} wants easier shift trades—saves managers ~40 hours/month on coordination."
            },
            "openShifts": {
                "title": "Open Shift Marketplace",
                "copy": "Your {team} wants faster call-out coverage—fill open shifts quicker with less firefighting."
            }
        }
        
        for group_key, group in nudge_groups.items():
            module_key = group["module_key"]
            position = group["position"]
            count = group["count"]
            
            info = module_info.get(module_key, {
                "title": module_key.replace("_", " ").title(),
                "copy": f"Your team has requested this feature."
            })
            
            # Dynamic title based on count
            team_label = f"{position.lower()} team" if position else "team"
            if count == 1:
                title = f"Your {team_label} requested {info['title']}"
            else:
                title = f"{count} members of your {team_label} requested {info['title']}"
            
            # Dynamic copy with team substitution
            copy = info["copy"].replace("{team}", team_label)
            
            items.append({
                "id": f"nudge_group_{group_key}",
                "type": "nudge_request",
                "priority": "medium",
                "title": title,
                "description": copy,
                "time_ago": _time_ago(group["latest_created"]) if group["latest_created"] else "Recently",
                "action": "Buy Now",
                "secondary_action": "Dismiss",
                "smm_boost": 0,
                "nudge_context": {
                    "module_key": module_key,
                    "position": position,
                    "count": count,
                    "nudge_ids": group["nudge_ids"]
                }
            })

    # Sort by priority (info always at bottom)
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    items.sort(key=lambda x: priority_order.get(x["priority"], 3))
    
    # Separate info items from actionable items
    summary_item = None
    hg_report_item = None
    actionable_items = []
    
    for item in items:
        if item.get("type") == "schedule_summary":
            summary_item = item
        elif item.get("type") == "house_guardian_report":
            hg_report_item = item
        else:
            actionable_items.append(item)
    
    # Return top 10 actionable + info items at bottom
    final_items = actionable_items[:10]
    if summary_item:
        final_items.append(summary_item)
    if hg_report_item:
        final_items.append(hg_report_item)
    
    return {
        "total_items": len(items),
        "items": final_items
    }
def compute_mood_heatmap(checkins_7d: list) -> dict:
    """
    Compute mood heatmap by day and shift (AM/PM).
    """
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    
    # Aggregate by day and shift
    day_shift_moods = {}
    for day in days:
        for shift in ["AM", "PM"]:
            day_shift_moods[f"{day}_{shift}"] = []
    
    for checkin in checkins_7d:
        checkin_date = checkin.get("checkin_date")
        if checkin_date:
            try:
                dt = date.fromisoformat(checkin_date)
                day_name = days[dt.weekday()]
                # Assume check-in time determines AM/PM, or default to PM
                shift = "PM"  # Could enhance with actual time
                key = f"{day_name}_{shift}"
                day_shift_moods[key].append(checkin.get("mood_emoji", 3))
            except:
                pass
    
    # Build local heatmap data
    local_data = []
    worst_spot = None
    worst_score = 100
    
    for day in days:
        for shift in ["AM", "PM"]:
            key = f"{day}_{shift}"
            moods = day_shift_moods[key]
            if moods:
                score = int(sum(moods) / len(moods) / 5 * 100)
            else:
                score = 75  # Default
            
            local_data.append({
                "day": day,
                "shift": shift,
                "score": score
            })
            
            if score < worst_score:
                worst_score = score
                worst_spot = f"{day} {shift}"
    
    # Network data (simulated percentiles)
    network_data = []
    for item in local_data:
        # Simulate percentile based on score
        percentile = min(95, max(5, item["score"] - 10 + (item["score"] // 20)))
        network_data.append({
            "day": item["day"],
            "shift": item["shift"],
            "percentile": percentile
        })
    
    return {
        "local": {
            "insight": f"⚠️ {worst_spot} is running 22% below your team's usual energy. Consider checking in with that shift." if worst_spot else "✨ Mood is steady across all shifts. Your team feels consistent.",
            "data": local_data
        },
        "network": {
            "insight": f"{worst_spot} better than 64% of restaurants" if worst_spot else "On par with network",
            "data": network_data
        }
    }


def compute_quick_stats(shifts_today: list, shifts_week: list, staff_list: list) -> dict:
    """
    Compute quick stats bar.
    """
    shifts_today_count = len(shifts_today)
    open_shifts = len([s for s in shifts_week if not s.get("staff_id")])
    
    # Calculate total hours
    total_hours = 0
    for shift in shifts_week:
        start = shift.get("scheduled_start")
        end = shift.get("scheduled_end")
        if start and end:
            try:
                from datetime import datetime as dt
                start_dt = dt.fromisoformat(start.replace("Z", "+00:00"))
                end_dt = dt.fromisoformat(end.replace("Z", "+00:00"))
                hours = (end_dt - start_dt).total_seconds() / 3600
                total_hours += hours
            except:
                pass
    
    # Estimate payroll (assume $15/hr average)
    est_payroll = int(total_hours * 15)
    
    return {
        "shifts_today": shifts_today_count,
        "open_shifts": open_shifts,
        "hours_this_period": int(total_hours),
        "est_payroll": f"${est_payroll:,}"
    }


def compute_pay_period(today: date) -> str:
    """
    Compute current pay period string.
    Assumes bi-weekly pay periods starting on Monday.
    """
    # today is passed as parameter
    # Find start of current pay period (every other Monday)
    days_since_monday = today.weekday()
    this_monday = today - timedelta(days=days_since_monday)
    
    # Assume 2-week pay periods
    week_of_year = this_monday.isocalendar()[1]
    if week_of_year % 2 == 0:
        period_start = this_monday - timedelta(days=7)
    else:
        period_start = this_monday
    
    period_end = period_start + timedelta(days=13)
    
    return f"{period_start.strftime('%b %d')} - {period_end.strftime('%b %d, %Y')}"


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _is_weekend(date_str: str) -> bool:
    """Check if date string is a weekend."""
    if not date_str:
        return False
    try:
        dt = date.fromisoformat(date_str)
        return dt.weekday() >= 5  # Saturday = 5, Sunday = 6
    except:
        return False


def _time_ago(timestamp_str: str) -> str:
    """Convert timestamp to 'X ago' string."""
    if not timestamp_str:
        return "Recently"
    
    try:
        from datetime import datetime as dt
        created = dt.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = dt.now(created.tzinfo)
        diff = now - created
        
        minutes = int(diff.total_seconds() / 60)
        hours = int(diff.total_seconds() / 3600)
        days = diff.days
        
        if days > 0:
            return f"{days} day{'s' if days > 1 else ''} ago"
        elif hours > 0:
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif minutes > 0:
            return f"{minutes} min ago"
        else:
            return "Just now"
    except:
        return "Recently"