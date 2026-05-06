"""
generate_synthetic_shifts.py

Generates synthetic shift data for 100 restaurants with varying coverage rates.
Coverage = shifts with staff assigned / total shifts needed.

Usage:
    cd C:\dev\restaurant-simulator\backend_final
    python generate_synthetic_shifts.py
"""

import csv
import hashlib
import random
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()

import os
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def load_staff_for_restaurant(organization_id: int) -> list:
    """Load staff from synthetic_staff_master for a restaurant."""
    result = supabase.table("synthetic_staff_master") \
        .select("staff_id, exit_day") \
        .eq("organization_id", organization_id) \
        .execute()
    return result.data or []


def deterministic_random(organization_id: int, day_index: int, salt: str = "") -> float:
    """Generate deterministic random [0,1) based on inputs."""
    seed_str = f"{organization_id}:{day_index}:{salt}"
    hash_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    return (hash_val % 1_000_000) / 1_000_000


def get_coverage_persona(organization_id: int) -> dict:
    """
    Assign a coverage persona to a restaurant.
    
    Distribution:
    - 25% excellent (92-98% coverage)
    - 35% good (85-92% coverage)
    - 25% struggling (75-85% coverage)
    - 15% poor (65-75% coverage)
    """
    seed = int(hashlib.sha256(f"coverage:{organization_id}".encode()).hexdigest(), 16)
    roll = (seed % 100) / 100
    
    if roll < 0.25:
        return {
            "type": "excellent",
            "base_coverage": 0.95,
            "variance": 0.03,
            "weekend_penalty": 0.02,
        }
    elif roll < 0.60:
        return {
            "type": "good",
            "base_coverage": 0.88,
            "variance": 0.05,
            "weekend_penalty": 0.05,
        }
    elif roll < 0.85:
        return {
            "type": "struggling",
            "base_coverage": 0.80,
            "variance": 0.07,
            "weekend_penalty": 0.08,
        }
    else:
        return {
            "type": "poor",
            "base_coverage": 0.70,
            "variance": 0.08,
            "weekend_penalty": 0.10,
        }


def get_restaurant_size(organization_id: int) -> int:
    """
    Determine how many shifts per day a restaurant needs.
    Based on restaurant profiles.
    """
    seed = int(hashlib.sha256(f"size:{organization_id}".encode()).hexdigest(), 16)
    roll = (seed % 100) / 100
    
    if roll < 0.20:
        return 8   # Small restaurant
    elif roll < 0.50:
        return 12  # Medium restaurant
    elif roll < 0.80:
        return 18  # Large restaurant
    else:
        return 25  # Very large restaurant


def generate_restaurant_shifts(
    organization_id: int,
    staff_list: list,
    total_days: int = 365,
) -> list:
    """
    Generate all shifts for a restaurant using real staff from synthetic_staff_master.
    """
    persona = get_coverage_persona(organization_id)
    shifts_per_day = get_restaurant_size(organization_id)

    shifts = []

    for day_index in range(1, total_days + 1):
        # Determine if weekend (days 6, 7, 13, 14, etc.)
        is_weekend = (day_index % 7) in [6, 0]
        day_type = "weekend" if is_weekend else "weekday"

        # Get staff who haven't exited yet
        available_staff = [
            s for s in staff_list 
            if s["exit_day"] is None or s["exit_day"] > day_index
        ]

        # Calculate coverage probability for this day
        daily_variance = (deterministic_random(organization_id, day_index, "var") - 0.5) * 2 * persona["variance"]
        weekend_penalty = persona["weekend_penalty"] if is_weekend else 0

        coverage_prob = persona["base_coverage"] + daily_variance - weekend_penalty
        coverage_prob = max(0.5, min(0.99, coverage_prob))  # Clamp to 50-99%

        # Generate shifts for the day
        # Mix of shift types
        shift_types = ["AM"] * 3 + ["PM"] * 4 + ["MID"] * 2 + ["FULL"] * 1

        for i in range(shifts_per_day):
            shift_type = shift_types[i % len(shift_types)]

            # Determine if this shift is covered
            is_covered = deterministic_random(organization_id, day_index, f"shift_{i}") < coverage_prob

            # Assign real staff_id from master table
            staff_id = None
            if is_covered and available_staff:
                staff_idx = int(deterministic_random(organization_id, day_index, f"staff_{i}") * len(available_staff))
                staff_id = available_staff[staff_idx]["staff_id"]

            shifts.append({
                "organization_id": organization_id,
                "staff_id": staff_id,
                "day_index": day_index,
                "shift_type": shift_type,
                "day_type": day_type,
                "is_covered": is_covered,
            })

    return shifts

def main():
    print("=" * 60)
    print("GENERATING SYNTHETIC SHIFTS")
    print("=" * 60)
    
    output_path = "synthetic_output/shifts.csv"
    
    all_shifts = []
    persona_counts = defaultdict(int)
    
    # Generate for restaurants 101-200
    for organization_id in range(101, 201):
        persona = get_coverage_persona(organization_id)
        persona_counts[persona["type"]] += 1

        staff_list = load_staff_for_restaurant(organization_id)
        shifts = generate_restaurant_shifts(organization_id, staff_list, total_days=365)
        all_shifts.extend(shifts)
        
        if organization_id % 20 == 0 or organization_id == 101:
            covered = sum(1 for s in shifts if s["is_covered"])
            total = len(shifts)
            pct = covered / total * 100
            print(f"  Restaurant {organization_id}: {persona['type']}, {pct:.1f}% coverage, {total} shifts")
    
    print(f"\nTotal shifts generated: {len(all_shifts):,}")
    
    # Write to CSV
    print(f"\nWriting to {output_path}...")
    
    fieldnames = [
        'organization_id',
        'staff_id',
        'day_index',
        'shift_type',
        'day_type',
        'is_covered',
    ]
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_shifts)
    
    print(f"  Wrote {len(all_shifts):,} records")
    
    # Summary stats
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    print("\nCoverage persona distribution:")
    for persona_type, count in sorted(persona_counts.items()):
        print(f"  {persona_type}: {count} restaurants")
    
    # Coverage distribution
    print("\nCoverage by restaurant (last 7 days only):")
    recent_shifts = [s for s in all_shifts if s["day_index"] > 358]
    
    restaurant_coverage = defaultdict(lambda: {"covered": 0, "total": 0})
    for shift in recent_shifts:
        rid = shift["organization_id"]
        restaurant_coverage[rid]["total"] += 1
        if shift["is_covered"]:
            restaurant_coverage[rid]["covered"] += 1
    
    coverage_rates = []
    for rid, data in restaurant_coverage.items():
        rate = data["covered"] / data["total"] * 100 if data["total"] > 0 else 0
        coverage_rates.append(rate)
    
    coverage_rates.sort()
    print(f"  Min: {min(coverage_rates):.1f}%")
    print(f"  Max: {max(coverage_rates):.1f}%")
    print(f"  Median: {coverage_rates[len(coverage_rates)//2]:.1f}%")
    print(f"  Average: {sum(coverage_rates)/len(coverage_rates):.1f}%")
    
    # Bucket distribution
    buckets = {"<70%": 0, "70-80%": 0, "80-90%": 0, "90-95%": 0, ">95%": 0}
    for rate in coverage_rates:
        if rate < 70:
            buckets["<70%"] += 1
        elif rate < 80:
            buckets["70-80%"] += 1
        elif rate < 90:
            buckets["80-90%"] += 1
        elif rate < 95:
            buckets["90-95%"] += 1
        else:
            buckets[">95%"] += 1
    
    print("\nCoverage distribution (last 7 days):")
    for bucket, count in buckets.items():
        print(f"  {bucket}: {count} restaurants")
    
    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"\nNext steps:")
    print(f"1. Run SQL: synthetic_shifts.sql in Supabase")
    print(f"2. Upload {output_path} to synthetic_shifts table")


if __name__ == "__main__":
    main()