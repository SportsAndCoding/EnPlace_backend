#!/usr/bin/env python3
"""
SYNTHETIC NOTES SEEDER
======================
Adds open-ended journal entries to synthetic_daily_emotions table.

Rules:
- 15% of entries get a note
- 10% of notes are positive (90% complaints/concerns)
- Content matches mood_emoji + felt_* flags + persona
- Seeds House Guardian danger patterns (rare)
- Seeds Frontline Intel drag patterns (common)

Usage:
    python seed_synthetic_notes.py --dry-run    # Preview only
    python seed_synthetic_notes.py              # Execute seeding
"""

import os
import random
import time
from datetime import datetime
from typing import Optional, Dict, List, Tuple

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✓ Loaded .env file")
except ImportError:
    print("⚠️  python-dotenv not installed. Install with: pip install python-dotenv")
    print("   Or set environment variables manually.")

from supabase import create_client, Client

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_KEY") or 
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or 
    os.environ.get("SUPABASE_KEY")
)

# Percentage of entries that get a note
NOTE_PROBABILITY = 0.15  # 15%

# Of those with notes, percentage that are positive
POSITIVE_PROBABILITY = 0.10  # 10%

# Manager personas get boosted positive probability
MANAGER_PERSONAS = ["emerging_leader", "quiet_pro"]
MANAGER_POSITIVE_BOOST = 0.20  # Additional 20% chance for managers

# Batch size for database updates
BATCH_SIZE = 500

# Delay between updates (seconds) - prevents rate limiting
REQUEST_DELAY = 0.05  # 50ms between requests = ~20 requests/second

# Retry settings
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

# ═══════════════════════════════════════════════════════════════════
# TEMPLATE BANKS
# ═══════════════════════════════════════════════════════════════════

# --- NEGATIVE TEMPLATES (by flag) ---

FAIRNESS_NEGATIVE = {
    "starters": [
        "Same people always get",
        "Why am I always stuck with",
        "It's not fair that",
        "I'm tired of",
        "So sick of",
        "Always me with",
    ],
    "topics": [
        "the worst sections",
        "closing every weekend",
        "covering call-outs",
        "the bad shifts",
        "last-minute changes",
        "doubles when nobody else does",
        "the slow stations",
        "all the side work",
        "training new people",
    ],
    "endings": [
        "Not fair.",
        "Whatever.",
        "I'm over it.",
        "Something has to change.",
        "Same story every week.",
        "",
        "",
    ],
}

RESPECT_NEGATIVE = [
    "Nobody listens.",
    "Management doesn't care.",
    "Talked down to again.",
    "Why do I bother saying anything?",
    "Invisible.",
    "Nobody asked for my input.",
    "Treated like I don't matter.",
    "Just a body to them.",
    "My ideas don't count here.",
    "Yelled at for something that wasn't my fault.",
]

SAFE_NEGATIVE = {
    "templates": [
        "The {equipment} is still broken.",
        "Asked about {issue} three times now.",
        "Nobody fixes anything around here.",
        "{equipment} is dangerous and nobody cares.",
        "Going to hurt myself on the {equipment} one day.",
        "Still waiting on someone to fix the {issue}.",
    ],
    "equipment": [
        "fryer", "walk-in door", "ice machine", "POS", "AC",
        "dishwasher", "grill", "oven", "slicer", "mixer",
        "reach-in", "heat lamp", "exhaust fan",
    ],
    "issues": [
        "leak in the back", "smell in dry storage", "wobbly ladder",
        "broken floor tile", "loose shelf", "flickering lights",
        "slippery floor by dish", "sharp edge on the prep table",
    ],
}

BURNOUT_NEGATIVE = [
    "Exhausted.",
    "Third double this week.",
    "Running on empty.",
    "Can't keep doing this.",
    "Need a break.",
    "Dead on my feet.",
    "Burned out.",
    "I'm fried.",
    "Don't know how much longer I can do this.",
    "Body is breaking down.",
    "Haven't had a day off in forever.",
]

NEUTRAL_MILD = [
    "Could be better.",
    "Just okay.",
    "Meh.",
    "Same old.",
    "Fine I guess.",
    "Nothing special.",
    "It is what it is.",
]

# --- POSITIVE TEMPLATES ---

POSITIVE_STANDARD = [
    "Good shift.",
    "Solid team tonight.",
    "Smooth service.",
    "No complaints.",
    "Actually felt good today.",
    "Nice flow today.",
    "Good energy on the floor.",
    "Teamwork was there.",
]

POSITIVE_MANAGER = [
    "Great teamwork today!",
    "Love this crew.",
    "Proud of how we handled the rush.",
    "Team really stepped up.",
    "This is why I love this job.",
    "Everyone pulled their weight.",
    "Really clicked as a team.",
    "Beautiful service tonight.",
    "Couldn't ask for a better shift.",
]

# --- HOUSE GUARDIAN: DANGER PATTERNS ---

DANGER_HARASSMENT = [
    "Uncomfortable with how closing has been going.",
    "Don't want to work with {name} anymore.",
    "Same thing keeps happening after close.",
    "{name} won't leave me alone.",
    "Feeling really uncomfortable around {name}.",
    "{name} said something inappropriate again.",
    "I don't feel safe closing with {name}.",
    "Need to talk to someone about {name}.",
    "{name} makes comments that cross the line.",
]

DANGER_THEFT = [
    "Pretty sure someone is taking from the register.",
    "Noticed money missing again.",
    "Something's off with the cash.",
    "Inventory keeps coming up short.",
    "Saw something I probably shouldn't have.",
    "Not sure I should say anything but...",
]

DANGER_SUBSTANCES = [
    "Someone showed up not sober again.",
    "Can't work with people in that condition.",
    "Smelled like alcohol.",
    "Definitely high.",
    "This is getting out of hand.",
    "Third time this week someone's been off.",
]

DANGER_HOSTILE = [
    "Screamed at in front of customers.",
    "The way {name} talks to people is not okay.",
    "Hostile environment.",
    "Don't feel safe here.",
    "Getting verbally abused.",
    "{name} threatened me.",
]

# Names for danger patterns (placeholder staff names)
DANGER_NAMES = [
    "Marcus", "Tyler", "Jake", "Derek", "Kyle",
    "Brandon", "Travis", "Chad", "Brett", "Cody",
]

# --- FRONTLINE INTEL: DRAG PATTERNS ---

DRAG_EQUIPMENT = [
    "Ice machine down again.",
    "POS is so slow on weekends.",
    "The {equipment} is broken. Again.",
    "How hard is it to fix the {equipment}?",
    "Dealing with the {equipment} every single shift.",
    "Customers complain about the {equipment} and I have to apologize.",
]

DRAG_PROCESS = [
    "The new closing checklist takes forever.",
    "Why do we do it this way?",
    "This process makes no sense.",
    "Would be so much easier if we just...",
    "Whoever designed this workflow never worked a shift.",
    "So many unnecessary steps.",
    "The sidework rotation is broken.",
]

DRAG_CUSTOMER = [
    "Customers get rude when we're out of stuff.",
    "Had to apologize for things that aren't my fault.",
    "Getting yelled at because the kitchen is slow.",
    "Dealt with an angry customer about the wait.",
    "People are so impatient.",
    "Customer complaints drain me.",
]

DRAG_IDEAS = [
    "Why don't we prep the sauces earlier?",
    "Would help if hosts could see the wait estimate.",
    "If I were the boss I'd change the section rotation.",
    "Simple fix: just move the expo station.",
    "We should cross-train more people.",
    "Wish someone would listen to the floor staff.",
    "I have ideas but nobody asks.",
    "If they'd just let us try something different.",
]

DRAG_EMOTIONAL_COST = [
    "Drained.",
    "Emotionally exhausted.",
    "This job is draining.",
    "Just tired.",
    "Feeling beat down.",
    "Hard to stay positive.",
    "Running on fumes.",
    "Can't fake the smile anymore.",
]

DRAG_EQUIPMENT_ITEMS = [
    "ice machine", "POS", "printer", "card reader", "oven",
    "coffee machine", "blender", "toaster", "warmer",
]


# ═══════════════════════════════════════════════════════════════════
# NOTE GENERATION LOGIC
# ═══════════════════════════════════════════════════════════════════

def should_get_note() -> bool:
    """15% chance of getting a note"""
    return random.random() < NOTE_PROBABILITY


def should_be_positive(persona: str) -> bool:
    """
    Determine if note should be positive.
    Base: 10% chance
    Managers: +20% additional chance
    """
    base_prob = POSITIVE_PROBABILITY
    if persona in MANAGER_PERSONAS:
        base_prob += MANAGER_POSITIVE_BOOST
    return random.random() < base_prob


def generate_fairness_complaint() -> str:
    """Generate a fairness-related complaint"""
    starter = random.choice(FAIRNESS_NEGATIVE["starters"])
    topic = random.choice(FAIRNESS_NEGATIVE["topics"])
    ending = random.choice(FAIRNESS_NEGATIVE["endings"])
    return f"{starter} {topic}. {ending}".strip()


def generate_safety_complaint() -> str:
    """Generate a safety-related complaint"""
    template = random.choice(SAFE_NEGATIVE["templates"])
    equipment = random.choice(SAFE_NEGATIVE["equipment"])
    issue = random.choice(SAFE_NEGATIVE["issues"])
    return template.format(equipment=equipment, issue=issue)


def generate_respect_complaint() -> str:
    """Generate a respect-related complaint"""
    return random.choice(RESPECT_NEGATIVE)


def generate_burnout_complaint() -> str:
    """Generate a burnout complaint"""
    return random.choice(BURNOUT_NEGATIVE)


def generate_positive_note(persona: str) -> str:
    """Generate a positive note"""
    if persona in MANAGER_PERSONAS:
        return random.choice(POSITIVE_MANAGER)
    return random.choice(POSITIVE_STANDARD)


def generate_neutral_note() -> str:
    """Generate a neutral/mild note"""
    return random.choice(NEUTRAL_MILD)


def generate_drag_note() -> str:
    """Generate a Frontline Intel drag note"""
    category = random.choice([
        "equipment", "process", "customer", "ideas", "emotional"
    ])
    
    if category == "equipment":
        template = random.choice(DRAG_EQUIPMENT)
        equipment = random.choice(DRAG_EQUIPMENT_ITEMS)
        return template.format(equipment=equipment)
    elif category == "process":
        return random.choice(DRAG_PROCESS)
    elif category == "customer":
        return random.choice(DRAG_CUSTOMER)
    elif category == "ideas":
        return random.choice(DRAG_IDEAS)
    else:
        return random.choice(DRAG_EMOTIONAL_COST)


def generate_note(
    mood: int,
    felt_fair: bool,
    felt_safe: bool,
    felt_respected: bool,
    persona: str
) -> Optional[str]:
    """
    Generate a note based on mood, flags, and persona.
    Returns None if no note should be generated.
    """
    # 15% chance of getting a note
    if not should_get_note():
        return None
    
    # High mood + all flags true = positive or skip
    if mood >= 4 and felt_fair and felt_safe and felt_respected:
        if should_be_positive(persona):
            return generate_positive_note(persona)
        return None  # Skip - nothing to say
    
    # Check if should be positive despite lower mood (rare)
    if should_be_positive(persona) and mood >= 3:
        return generate_positive_note(persona)
    
    # Mood 3 with all flags true = neutral or skip
    if mood == 3 and felt_fair and felt_safe and felt_respected:
        if random.random() < 0.3:  # 30% chance of neutral comment
            return generate_neutral_note()
        return None
    
    # Low mood (1-2) = complaint based on false flags
    if mood <= 2:
        # Prioritize which flag to complain about
        complaints = []
        if not felt_fair:
            complaints.append("fairness")
        if not felt_respected:
            complaints.append("respect")
        if not felt_safe:
            complaints.append("safety")
        
        if not complaints:
            # All flags true but low mood = burnout
            return generate_burnout_complaint()
        
        complaint_type = random.choice(complaints)
        
        if complaint_type == "fairness":
            return generate_fairness_complaint()
        elif complaint_type == "respect":
            return generate_respect_complaint()
        else:
            return generate_safety_complaint()
    
    # Mood 3 with some false flags = mild complaint or drag
    if mood == 3:
        if random.random() < 0.5:
            return generate_neutral_note()
        else:
            return generate_drag_note()
    
    # Mood 4-5 with some false flag = drag (not complaint)
    if mood >= 4:
        if random.random() < 0.4:
            return generate_drag_note()
        return generate_positive_note(persona)
    
    return None


# ═══════════════════════════════════════════════════════════════════
# HOUSE GUARDIAN DANGER PATTERN SEEDING
# ═══════════════════════════════════════════════════════════════════

def seed_danger_patterns(
    supabase: Client,
    organization_id: int,
    staff_records: List[Dict]
) -> List[Tuple[int, str]]:
    """
    Seed specific danger patterns for House Guardian demo.
    Returns list of (emotion_id, note) to update.
    
    Patterns:
    1. Harassment cluster - 3 staff, same week, closing shift references
    2. Theft signal - 1-2 mentions
    3. Substance mention - 1 mention
    """
    updates = []
    danger_name = random.choice(DANGER_NAMES)
    
    # Get emotion records for this restaurant
    result = supabase.table("synthetic_daily_emotions") \
        .select("id, staff_id, day_index, mood_emoji, felt_safe, felt_respected") \
        .eq("organization_id", organization_id) \
        .lte("mood_emoji", 2) \
        .limit(100) \
        .execute()
    
    if not result.data or len(result.data) < 10:
        return updates
    
    emotions = result.data
    
    # --- Pattern 1: Harassment cluster (3 entries, close day_index) ---
    # Find 3 entries with low mood and felt_respected=False or felt_safe=False
    harassment_candidates = [
        e for e in emotions 
        if not e.get("felt_respected") or not e.get("felt_safe")
    ]
    
    if len(harassment_candidates) >= 3:
        # Pick 3 with similar day_index (within 5 days)
        base_day = harassment_candidates[0].get("day_index", 100)
        cluster = [
            e for e in harassment_candidates 
            if abs(e.get("day_index", 0) - base_day) <= 5
        ][:3]
        
        if len(cluster) >= 3:
            harassment_notes = [
                "Uncomfortable with how closing has been going.",
                f"Don't want to work with {danger_name} anymore.",
                "Same thing keeps happening after close.",
            ]
            for i, entry in enumerate(cluster):
                updates.append((entry["id"], harassment_notes[i]))
    
    # --- Pattern 2: Theft signal (1-2 entries) ---
    theft_candidates = [
        e for e in emotions 
        if e["id"] not in [u[0] for u in updates]
    ][:2]
    
    if theft_candidates:
        theft_notes = random.sample(DANGER_THEFT, min(2, len(theft_candidates)))
        for i, entry in enumerate(theft_candidates):
            updates.append((entry["id"], theft_notes[i]))
    
    # --- Pattern 3: Substance mention (1 entry) ---
    substance_candidates = [
        e for e in emotions 
        if e["id"] not in [u[0] for u in updates]
    ][:1]
    
    if substance_candidates:
        updates.append((
            substance_candidates[0]["id"],
            random.choice(DANGER_SUBSTANCES)
        ))
    
    return updates


# ═══════════════════════════════════════════════════════════════════
# MAIN SEEDING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def add_notes_column(supabase: Client) -> bool:
    """
    Add notes column to synthetic_daily_emotions if it doesn't exist.
    Returns True if column exists or was added.
    """
    # Check if column exists
    try:
        result = supabase.table("synthetic_daily_emotions") \
            .select("notes") \
            .limit(1) \
            .execute()
        print("✓ 'notes' column already exists")
        return True
    except Exception as e:
        if "column" in str(e).lower() and "does not exist" in str(e).lower():
            print("Adding 'notes' column to synthetic_daily_emotions...")
            # Note: This requires running SQL directly
            # Supabase client doesn't support ALTER TABLE
            print("⚠️  Please run this SQL manually:")
            print("ALTER TABLE synthetic_daily_emotions ADD COLUMN notes TEXT;")
            return False
        raise e


def get_staff_personas(supabase: Client) -> Dict[str, str]:
    """Get mapping of staff_id -> start_persona"""
    result = supabase.table("synthetic_staff_master") \
        .select("staff_id, start_persona") \
        .execute()
    
    return {
        row["staff_id"]: row["start_persona"] 
        for row in (result.data or [])
    }


def update_with_retry(supabase: Client, emotion_id: int, note: str) -> bool:
    """Update a record with retry logic"""
    for attempt in range(MAX_RETRIES):
        try:
            supabase.table("synthetic_daily_emotions") \
                .update({"notes": note}) \
                .eq("id", emotion_id) \
                .execute()
            time.sleep(REQUEST_DELAY)
            return True
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"    Retry {attempt + 1}/{MAX_RETRIES} after error: {str(e)[:50]}...")
                time.sleep(RETRY_DELAY * (attempt + 1))  # Exponential backoff
            else:
                print(f"    ❌ Failed after {MAX_RETRIES} attempts: {emotion_id}")
                return False
    return False


def seed_notes(supabase: Client, dry_run: bool = False):
    """Main seeding function"""
    
    print("=" * 60)
    print("SYNTHETIC NOTES SEEDER")
    print("=" * 60)
    
    # Check/add column
    if not dry_run:
        if not add_notes_column(supabase):
            print("\n❌ Cannot proceed without 'notes' column.")
            print("   Run the ALTER TABLE command and try again.")
            return
    
    # Get staff personas
    print("\nLoading staff personas...")
    personas = get_staff_personas(supabase)
    print(f"  Found {len(personas)} staff members")
    
    # Get all restaurants
    print("\nLoading restaurants...")
    restaurants = supabase.table("synthetic_organizations") \
        .select("organization_id") \
        .execute()
    restaurant_ids = [r["organization_id"] for r in (restaurants.data or [])]
    print(f"  Found {len(restaurant_ids)} restaurants")
    
    # Count total emotions
    print("\nCounting emotion records...")
    count_result = supabase.table("synthetic_daily_emotions") \
        .select("id", count="exact") \
        .execute()
    total_emotions = count_result.count or 0
    print(f"  Total emotion records: {total_emotions:,}")
    
    # Count already-seeded (for resume capability)
    seeded_result = supabase.table("synthetic_daily_emotions") \
        .select("id", count="exact") \
        .not_.is_("notes", "null") \
        .execute()
    already_seeded = seeded_result.count or 0
    if already_seeded > 0:
        print(f"  Already seeded: {already_seeded:,} (will skip these)")
    
    expected_notes = int(total_emotions * NOTE_PROBABILITY)
    print(f"  Expected notes to generate: ~{expected_notes:,}")
    
    if dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN - No changes will be made")
        print("=" * 60)
        
        # Sample a few generations
        print("\nSample generated notes:")
        samples = [
            (1, False, True, True, "burned_idealist"),
            (2, True, False, True, "workhorse"),
            (3, True, True, False, "snarky_rookie"),
            (4, True, True, True, "enthusiastic_rookie"),
            (5, True, True, True, "emerging_leader"),
            (2, False, False, True, "ghoster_in_training"),
        ]
        
        for mood, fair, safe, respected, persona in samples:
            # Force note generation for sample
            original_prob = NOTE_PROBABILITY
            for _ in range(5):  # Try a few times
                note = generate_note(mood, fair, safe, respected, persona)
                if note:
                    break
            
            flags = []
            if not fair: flags.append("fair=F")
            if not safe: flags.append("safe=F")
            if not respected: flags.append("resp=F")
            flag_str = ", ".join(flags) if flags else "all=T"
            
            print(f"\n  Mood={mood}, {flag_str}, {persona}:")
            print(f"  → \"{note or '[no note]'}\"")
        
        print("\n" + "=" * 60)
        print("Run without --dry-run to execute seeding")
        print("=" * 60)
        return
    
    # Process in batches by restaurant
    total_updated = 0
    total_failed = 0
    danger_patterns_seeded = 0
    
    for i, organization_id in enumerate(restaurant_ids):
        print(f"\nProcessing restaurant {organization_id} ({i+1}/{len(restaurant_ids)})...")
        
        # Seed danger patterns for first 3 restaurants (demo purposes)
        if i < 3:
            danger_updates = seed_danger_patterns(supabase, organization_id, [])
            if danger_updates:
                print(f"  Seeding {len(danger_updates)} danger patterns...")
                for emotion_id, note in danger_updates:
                    if update_with_retry(supabase, emotion_id, note):
                        danger_patterns_seeded += 1
        
        # Get emotions for this restaurant (that don't already have notes)
        offset = 0
        restaurant_updated = 0
        
        while True:
            try:
                result = supabase.table("synthetic_daily_emotions") \
                    .select("id, staff_id, mood_emoji, felt_safe, felt_fair, felt_respected") \
                    .eq("organization_id", organization_id) \
                    .is_("notes", "null") \
                    .range(offset, offset + BATCH_SIZE - 1) \
                    .execute()
            except Exception as e:
                print(f"  ⚠️  Error fetching batch, waiting 10s: {str(e)[:50]}...")
                time.sleep(10)
                continue
            
            if not result.data:
                break
            
            updates = []
            for row in result.data:
                persona = personas.get(row["staff_id"], "enthusiastic_rookie")
                note = generate_note(
                    mood=row.get("mood_emoji", 3),
                    felt_fair=row.get("felt_fair", True),
                    felt_safe=row.get("felt_safe", True),
                    felt_respected=row.get("felt_respected", True),
                    persona=persona
                )
                
                if note:
                    updates.append((row["id"], note))
            
            # Update with retry and delay
            for emotion_id, note in updates:
                if update_with_retry(supabase, emotion_id, note):
                    restaurant_updated += 1
                else:
                    total_failed += 1
            
            offset += BATCH_SIZE
            
            # Progress indicator
            if offset % 2000 == 0:
                print(f"    ... processed {offset} records, {restaurant_updated} notes added")
            
            if len(result.data) < BATCH_SIZE:
                break
        
        total_updated += restaurant_updated
        print(f"  ✓ Updated {restaurant_updated} records")
    
    print("\n" + "=" * 60)
    print("SEEDING COMPLETE")
    print("=" * 60)
    print(f"Total notes generated: {total_updated:,}")
    print(f"Danger patterns seeded: {danger_patterns_seeded}")
    print(f"Failed updates: {total_failed}")
    print(f"Expected: ~{expected_notes:,}")


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    dry_run = "--dry-run" in sys.argv
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Missing environment variables:")
        print("   SUPABASE_URL")
        print("   SUPABASE_KEY or SUPABASE_SERVICE_KEY")
        sys.exit(1)
    
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    seed_notes(supabase, dry_run=dry_run)