"""
seed_social_graph.py

Populates Demo Bistro (restaurant_id=1) with realistic social graph data:
  - staff_graph_metrics: 25 staff × 10 dates (every 3 days for 30 days)
  - staff_graph_edges: ~55 relationship edges
  - staff_cascade_analysis: 4 critical/important staff what-if analyses

Run: heroku run "python seed_social_graph.py" --app enplace-api-v3
"""

import random
from datetime import date, timedelta
from database.supabase_client import get_supabase

supabase = get_supabase()
RESTAURANT_ID = 1

# ═══════════════════════════════════════════════════════════════
# STAFF ROSTER (25 key staff for the graph)
# ═══════════════════════════════════════════════════════════════

STAFF = [
    # staff_id,         position,      role_label,     priority_tier, base_retention, flight_risk, graph_role
    ("STAFF010", "General Manager",    "GM",           "critical",    92, 0.05, "hub"),
    ("STAFF013", "Executive Chef",     "Exec Chef",    "critical",    88, 0.08, "glue_person"),
    ("STAFF011", "Asst Manager",       "Asst Mgr",     "important",   82, 0.12, "hub"),
    ("STAFF014", "Sous Chef",          "Sous Chef",    "important",   78, 0.15, "bridge"),
    ("BAR001",   "Bartender",          "Bartender",    "important",   76, 0.18, "bridge"),
    ("SRV001",   "Server",             "Server",       "important",   74, 0.14, "glue_person"),
    ("COK001",   "Line Cook",          "Line Cook",    "standard",    70, 0.22, "hub"),
    ("COK002",   "Line Cook",          "Line Cook",    "standard",    65, 0.28, "peripheral"),
    ("COK003",   "Line Cook",          "Line Cook",    "standard",    68, 0.25, "peripheral"),
    ("SRV002",   "Server",             "Server",       "standard",    72, 0.20, "bridge"),
    ("SRV003",   "Server",             "Server",       "standard",    66, 0.30, "peripheral"),
    ("STAFF027", "Server",             "Server",       "standard",    71, 0.19, "peripheral"),
    ("STAFF028", "Server",             "Server",       "standard",    69, 0.24, "peripheral"),
    ("STAFF029", "Server",             "Server",       "standard",    67, 0.26, "peripheral"),
    ("BAR002",   "Bartender",          "Bartender",    "standard",    73, 0.17, "bridge"),
    ("STAFF004", "Bartender",          "Bartender",    "standard",    64, 0.32, "peripheral"),
    ("HST001",   "Host",               "Host",         "standard",    70, 0.21, "bridge"),
    ("STAFF047", "Host",               "Host",         "low",         62, 0.35, "peripheral"),
    ("BUS001",   "Busser",             "Busser",       "standard",    63, 0.33, "peripheral"),
    ("STAFF051", "Busser",             "Busser",       "low",         58, 0.40, "peripheral"),
    ("DSH001",   "Dishwasher",         "Dishwasher",   "low",         55, 0.45, "peripheral"),
    ("DSH002",   "Dishwasher",         "Dishwasher",   "low",         50, 0.52, "peripheral"),
    ("STAFF002", "Line Cook",          "Line Cook",    "standard",    66, 0.29, "peripheral"),
    ("STAFF016", "Line Cook",          "Line Cook",    "standard",    68, 0.27, "peripheral"),
    ("STAFF043", "Bartender",          "Bartender",    "standard",    70, 0.22, "peripheral"),
]

# Tier -> color mapping
TIER_COLORS = {
    "critical": "#DC2626",
    "important": "#F97316",
    "standard": "#3B82F6",
    "low": "#9CA3AF",
}

# Graph role -> icon mapping
ROLE_ICONS = {
    "hub": "star",
    "glue_person": "heart",
    "bridge": "git-branch",
    "peripheral": "circle",
}

# Mood colors (based on retention score)
def mood_color(score):
    if score >= 80: return "#22C55E"
    if score >= 65: return "#84CC16"
    if score >= 50: return "#EAB308"
    if score >= 35: return "#F97316"
    return "#DC2626"

# Size factor (based on graph role)
SIZE_FACTORS = {
    "hub": 0.9,
    "glue_person": 0.85,
    "bridge": 0.65,
    "peripheral": 0.4,
}


# ═══════════════════════════════════════════════════════════════
# EDGES (who works with whom)
# ═══════════════════════════════════════════════════════════════

# Realistic connections: managers connect to everyone,
# kitchen staff connect to each other + expo bridge,
# FOH connects to FOH + hosts, etc.
EDGES = [
    # (staff_a, staff_b, weight, primary_type)
    # GM connections (hub — connects to leadership + key staff)
    ("STAFF010", "STAFF013", 0.90, "shift_cowork"),
    ("STAFF010", "STAFF011", 0.85, "shift_cowork"),
    ("STAFF010", "STAFF014", 0.70, "shift_cowork"),
    ("STAFF010", "BAR001",   0.60, "shift_cowork"),
    ("STAFF010", "SRV001",   0.55, "shift_cowork"),
    ("STAFF010", "HST001",   0.50, "shift_cowork"),
    ("STAFF010", "COK001",   0.45, "shift_cowork"),

    # Exec Chef connections (glue — holds kitchen together)
    ("STAFF013", "STAFF014", 0.92, "shift_cowork"),
    ("STAFF013", "COK001",   0.85, "shift_cowork"),
    ("STAFF013", "COK002",   0.80, "shift_cowork"),
    ("STAFF013", "COK003",   0.78, "shift_cowork"),
    ("STAFF013", "STAFF002", 0.72, "shift_cowork"),
    ("STAFF013", "STAFF016", 0.70, "shift_cowork"),
    ("STAFF013", "STAFF011", 0.55, "shift_cowork"),
    ("STAFF013", "DSH001",   0.40, "shift_cowork"),

    # Sous Chef connections (bridge — kitchen to FOH)
    ("STAFF014", "COK001",   0.82, "shift_cowork"),
    ("STAFF014", "COK002",   0.75, "shift_cowork"),
    ("STAFF014", "COK003",   0.72, "shift_cowork"),
    ("STAFF014", "SRV001",   0.45, "shift_cowork"),  # expo bridge
    ("STAFF014", "STAFF002", 0.68, "shift_cowork"),

    # Asst Manager connections
    ("STAFF011", "SRV001",   0.75, "shift_cowork"),
    ("STAFF011", "SRV002",   0.70, "shift_cowork"),
    ("STAFF011", "BAR001",   0.65, "shift_cowork"),
    ("STAFF011", "HST001",   0.60, "shift_cowork"),
    ("STAFF011", "STAFF027", 0.55, "shift_cowork"),
    ("STAFF011", "BUS001",   0.45, "shift_cowork"),

    # Kitchen internal bonds
    ("COK001", "COK002",   0.70, "shift_cowork"),
    ("COK001", "COK003",   0.65, "shift_cowork"),
    ("COK001", "STAFF002", 0.60, "shift_cowork"),
    ("COK002", "COK003",   0.72, "shift_cowork"),
    ("COK002", "STAFF016", 0.55, "shift_cowork"),
    ("COK003", "STAFF002", 0.50, "shift_cowork"),
    ("STAFF002", "STAFF016", 0.62, "shift_cowork"),
    ("STAFF016", "DSH001",  0.35, "shift_cowork"),
    ("COK001", "DSH001",   0.30, "shift_cowork"),

    # Server team bonds
    ("SRV001", "SRV002",    0.78, "shift_cowork"),
    ("SRV001", "STAFF027",  0.65, "shift_cowork"),
    ("SRV001", "STAFF028",  0.60, "shift_cowork"),
    ("SRV002", "STAFF029",  0.55, "shift_cowork"),
    ("SRV002", "STAFF027",  0.50, "shift_cowork"),
    ("SRV003", "STAFF028",  0.48, "shift_cowork"),
    ("SRV003", "STAFF029",  0.45, "shift_cowork"),
    ("STAFF027", "STAFF028", 0.58, "shift_cowork"),
    ("STAFF028", "STAFF029", 0.52, "shift_cowork"),

    # Bar team bonds
    ("BAR001", "BAR002",    0.82, "shift_cowork"),
    ("BAR001", "STAFF004",  0.60, "shift_cowork"),
    ("BAR001", "STAFF043",  0.55, "shift_cowork"),
    ("BAR002", "STAFF004",  0.50, "shift_cowork"),
    ("BAR002", "STAFF043",  0.48, "shift_cowork"),

    # FOH cross-connections (servers ↔ hosts ↔ bussers)
    ("HST001",   "SRV001",   0.55, "shift_cowork"),
    ("HST001",   "STAFF047", 0.62, "shift_cowork"),
    ("HST001",   "BUS001",   0.48, "shift_cowork"),
    ("BUS001",   "SRV001",   0.42, "shift_cowork"),
    ("BUS001",   "STAFF051", 0.55, "shift_cowork"),
    ("STAFF047", "STAFF051", 0.35, "shift_cowork"),

    # Swap/coverage bonds (cross-department — these are high signal)
    ("SRV001", "BAR001",    0.40, "swap_pickup"),
    ("SRV002", "HST001",    0.35, "swap_pickup"),
    ("COK001", "STAFF014",  0.30, "osm_pickup"),
    ("BAR002", "SRV003",    0.25, "swap_pickup"),

    # Mood sync bonds (emotional alignment)
    ("SRV001", "STAFF013",  0.30, "mood_sync"),
    ("BAR001", "SRV002",    0.28, "mood_sync"),
    ("DSH002", "STAFF051",  0.35, "mood_sync"),
]


# ═══════════════════════════════════════════════════════════════
# SEED FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def seed_metrics():
    """Seed staff_graph_metrics for today + every 3 days going back 30 days."""
    today = date.today()
    dates = [today - timedelta(days=i*3) for i in range(11)]  # 11 dates, 0-30 days back
    dates.reverse()  # oldest first

    rows = []
    for calc_date in dates:
        day_offset = (today - calc_date).days
        for s in STAFF:
            sid, position, role_label, tier, base_ret, base_flight, graph_role = s

            # Add gentle drift over time (retention improves slightly toward present)
            drift = random.uniform(-3, 3) + (day_offset * -0.05)
            retention = max(30, min(98, base_ret + drift))
            flight = max(0.02, min(0.7, base_flight + random.uniform(-0.04, 0.04)))

            # Centrality scores based on graph role
            if graph_role == "hub":
                between = round(random.uniform(0.15, 0.35), 4)
                eigen = round(random.uniform(0.25, 0.45), 4)
                degree = round(random.uniform(0.30, 0.50), 4)
                connected = random.randint(5, 8)
            elif graph_role == "glue_person":
                between = round(random.uniform(0.20, 0.40), 4)
                eigen = round(random.uniform(0.20, 0.38), 4)
                degree = round(random.uniform(0.25, 0.42), 4)
                connected = random.randint(5, 9)
            elif graph_role == "bridge":
                between = round(random.uniform(0.10, 0.25), 4)
                eigen = round(random.uniform(0.12, 0.28), 4)
                degree = round(random.uniform(0.15, 0.30), 4)
                connected = random.randint(3, 6)
            else:  # peripheral
                between = round(random.uniform(0.01, 0.10), 4)
                eigen = round(random.uniform(0.03, 0.15), 4)
                degree = round(random.uniform(0.05, 0.18), 4)
                connected = random.randint(1, 4)

            composite = round((between * 0.4 + eigen * 0.35 + degree * 0.25), 4)
            cascade_risk_val = round(composite * flight * 100, 2)

            # Cascade severity from cascade_risk
            if cascade_risk_val > 5:
                casc_sev = "critical"
            elif cascade_risk_val > 2:
                casc_sev = "high"
            elif cascade_risk_val > 0.5:
                casc_sev = "moderate"
            else:
                casc_sev = "low"

            # Top reason text
            reasons = {
                "critical": [
                    "Highest network centrality — losing them fractures the team",
                    "Key connector across departments — irreplaceable relationships",
                    "Anchor of the kitchen line — departure triggers cascade",
                ],
                "important": [
                    "Strong cross-department bridge — hard to replace connections",
                    "High swap/coverage participation — team relies on their flexibility",
                    "Experienced leader — newer staff depend on their guidance",
                ],
                "standard": [
                    "Solid contributor with moderate network ties",
                    "Reliable shift worker — standard replacement difficulty",
                    "Team player with growing connections",
                ],
                "low": [
                    "New or peripheral — limited network impact",
                    "Few connections — departure is contained",
                    "Low engagement in team activities",
                ],
            }
            top_reason = random.choice(reasons[tier])

            # Worst case exits
            worst = 0
            if tier == "critical": worst = random.randint(3, 6)
            elif tier == "important": worst = random.randint(1, 3)
            elif tier == "standard": worst = random.randint(0, 1)

            rows.append({
                "staff_id": sid,
                "restaurant_id": RESTAURANT_ID,
                "calculated_date": calc_date.isoformat(),
                "betweenness_centrality": between,
                "eigenvector_centrality": eigen,
                "degree_centrality": degree,
                "composite_criticality": composite,
                "role_label": role_label,
                "priority_tier": tier,
                "retention_score": round(retention, 1),
                "cascade_risk": cascade_risk_val,
                "cascade_severity": casc_sev,
                "worst_case_exits": worst,
                "flight_risk": round(flight, 3),
                "replacement_difficulty": round(random.uniform(0.3, 0.9), 2),
                "top_reason": top_reason,
                "connected_staff_count": connected,
                "strongest_connection_id": None,
                "mood_color": mood_color(retention),
                "tier_color": TIER_COLORS[tier],
                "role_icon": ROLE_ICONS[graph_role],
                "size_factor": SIZE_FACTORS[graph_role],
            })

    # Batch insert (chunk to avoid payload limits)
    print(f"Inserting {len(rows)} metric rows...")
    for i in range(0, len(rows), 50):
        chunk = rows[i:i+50]
        supabase.table("staff_graph_metrics").upsert(
            chunk,
            on_conflict="staff_id,restaurant_id,calculated_date"
        ).execute()
    print(f"  ✓ {len(rows)} metrics inserted across {len(set(r['calculated_date'] for r in rows))} dates")


def seed_edges():
    """Seed staff_graph_edges."""
    today = date.today()
    rows = []

    for a, b, weight, primary_type in EDGES:
        # Build edge_type_weights — primary type gets most weight, sprinkle others
        type_weights = {"shift_cowork": 0, "swap_pickup": 0, "osm_pickup": 0, "mood_sync": 0}
        type_weights[primary_type] = round(weight * 0.7, 3)

        # Add minor secondary weights
        secondary_types = [t for t in type_weights if t != primary_type]
        for st in secondary_types:
            type_weights[st] = round(random.uniform(0, weight * 0.15), 3)

        # Enforce chk_edge_ordering: staff_id_a < staff_id_b alphabetically
        id_a, id_b = (a, b) if a < b else (b, a)

        rows.append({
            "restaurant_id": RESTAURANT_ID,
            "staff_id_a": id_a,
            "staff_id_b": id_b,
            "weight": round(weight, 3),
            "edge_type_weights": type_weights,
            "last_interaction_date": (today - timedelta(days=random.randint(0, 5))).isoformat(),
        })

    print(f"Inserting {len(rows)} edges...")
    supabase.table("staff_graph_edges").upsert(
        rows,
        on_conflict="restaurant_id,staff_id_a,staff_id_b"
    ).execute()
    print(f"  ✓ {len(rows)} edges inserted")


def seed_cascade_analysis():
    """Seed cascade analysis for critical/important staff."""
    today = date.today()

    analyses = [
        {
            "target_staff_id": "STAFF010",
            "cascade_severity": "critical",
            "expected_additional_exits": 3.2,
            "worst_case_exits": 6,
            "total_departure_estimate": 4.2,
            "cost_multiplier": 4.8,
            "risk_narrative": (
                "James Smith is the operational backbone of Demo Bistro. As GM, he directly manages "
                "the assistant manager, executive chef, and bar lead. His departure would create a "
                "leadership vacuum affecting all departments. The kitchen team (6 connected staff) and "
                "FOH leadership would lose their primary decision-maker. Expected 3-4 follow exits "
                "within 60 days, primarily from management and senior kitchen staff who rely on his "
                "direction. Worst case: 6 exits including the sous chef and lead bartender."
            ),
            "at_risk_staff": [
                {"staff_id": "STAFF011", "follow_probability": 0.35, "reason": "Direct report — would lose mentor and advocate"},
                {"staff_id": "STAFF013", "follow_probability": 0.28, "reason": "Long working relationship — GM is their operational partner"},
                {"staff_id": "STAFF014", "follow_probability": 0.22, "reason": "Sous chef depends on GM for scheduling and conflict resolution"},
                {"staff_id": "BAR001",   "follow_probability": 0.18, "reason": "Bar lead has strong loyalty to current GM specifically"},
                {"staff_id": "SRV001",   "follow_probability": 0.12, "reason": "Senior server — would lose primary advocate for schedule requests"},
                {"staff_id": "HST001",   "follow_probability": 0.08, "reason": "Host team relies on GM for customer escalations"},
            ],
        },
        {
            "target_staff_id": "STAFF013",
            "cascade_severity": "critical",
            "expected_additional_exits": 2.8,
            "worst_case_exits": 5,
            "total_departure_estimate": 3.8,
            "cost_multiplier": 4.2,
            "risk_narrative": (
                "Jennifer Martinez is the glue of the kitchen. Every line cook and prep cook connects "
                "through her — she runs the pass, trains new hires, and mediates kitchen conflicts. "
                "Losing her doesn't just remove a chef; it removes the person who makes the kitchen "
                "function as a team. The sous chef would be overwhelmed, line cooks would lose their "
                "anchor, and dish staff would feel the culture shift immediately. High probability of "
                "2-3 kitchen exits within 45 days."
            ),
            "at_risk_staff": [
                {"staff_id": "STAFF014", "follow_probability": 0.40, "reason": "Sous chef — would inherit impossible workload"},
                {"staff_id": "COK001",   "follow_probability": 0.30, "reason": "Senior line cook — trained by and loyal to exec chef"},
                {"staff_id": "COK002",   "follow_probability": 0.25, "reason": "Line cook — would lose primary mentor"},
                {"staff_id": "STAFF002", "follow_probability": 0.20, "reason": "Line cook — connected through daily shift overlap"},
                {"staff_id": "DSH001",   "follow_probability": 0.10, "reason": "Dishwasher — would feel kitchen morale drop"},
            ],
        },
        {
            "target_staff_id": "BAR001",
            "cascade_severity": "high",
            "expected_additional_exits": 1.5,
            "worst_case_exits": 3,
            "total_departure_estimate": 2.5,
            "cost_multiplier": 2.8,
            "risk_narrative": (
                "The lead bartender is a bridge between the bar team and FOH servers. They cover shifts "
                "across departments and have the strongest swap network of any staff member. Losing them "
                "would isolate the remaining bartenders and remove a critical coverage resource. The bar "
                "team is tight — BAR002 and STAFF004 both have moderate follow-exit probability."
            ),
            "at_risk_staff": [
                {"staff_id": "BAR002",    "follow_probability": 0.30, "reason": "Close working partner — they run the bar together"},
                {"staff_id": "STAFF004",  "follow_probability": 0.20, "reason": "Junior bartender — would lose training and mentorship"},
                {"staff_id": "STAFF043",  "follow_probability": 0.15, "reason": "Part-time bartender — may not stay without team cohesion"},
            ],
        },
        {
            "target_staff_id": "SRV001",
            "cascade_severity": "high",
            "expected_additional_exits": 1.2,
            "worst_case_exits": 3,
            "total_departure_estimate": 2.2,
            "cost_multiplier": 2.4,
            "risk_narrative": (
                "This senior server is a glue person — they connect the server team, bridge to the "
                "kitchen via expo, and participate heavily in shift swaps. Their departure would weaken "
                "the server team's cohesion and remove a key expo bridge. Newer servers who trained "
                "under them would lose their go-to resource."
            ),
            "at_risk_staff": [
                {"staff_id": "SRV002",    "follow_probability": 0.22, "reason": "Close working partner on most shifts"},
                {"staff_id": "STAFF027",  "follow_probability": 0.15, "reason": "Newer server — trained by and relies on senior"},
                {"staff_id": "STAFF028",  "follow_probability": 0.12, "reason": "Shift partner — would lose preferred close partner"},
            ],
        },
    ]

    rows = []
    for a in analyses:
        rows.append({
            "restaurant_id": RESTAURANT_ID,
            "target_staff_id": a["target_staff_id"],
            "analysis_date": today.isoformat(),
            "cascade_severity": a["cascade_severity"],
            "expected_additional_exits": a["expected_additional_exits"],
            "worst_case_exits": a["worst_case_exits"],
            "total_departure_estimate": a["total_departure_estimate"],
            "cost_multiplier": a["cost_multiplier"],
            "risk_narrative": a["risk_narrative"],
            "at_risk_staff": a["at_risk_staff"],
            "cascade_viz_before": {},
            "cascade_viz_after": {},
            "removed_edges": [],
        })

    print(f"Inserting {len(rows)} cascade analyses...")
    supabase.table("staff_cascade_analysis").insert(rows).execute()
    print(f"  ✓ {len(rows)} cascade analyses inserted")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("SOCIAL GRAPH SEED — Demo Bistro")
    print("=" * 60)
    print()

    # Clean existing demo data first
    print("Cleaning existing graph data for restaurant_id=1...")
    supabase.table("staff_graph_metrics").delete().eq("restaurant_id", RESTAURANT_ID).execute()
    supabase.table("staff_graph_edges").delete().eq("restaurant_id", RESTAURANT_ID).execute()
    supabase.table("staff_cascade_analysis").delete().eq("restaurant_id", RESTAURANT_ID).execute()
    print("  ✓ Cleaned\n")

    seed_metrics()
    print()
    seed_edges()
    print()
    seed_cascade_analysis()

    print()
    print("=" * 60)
    print("DONE — Reload the Social Graph page to see it live")
    print("=" * 60)