"""
generate_manager_logs.py

Generates synthetic manager logs from existing synthetic emotion/behavior data.
Run this after the main simulation has generated daily_emotions and daily_behavior CSVs.

Usage:
    cd C:\dev\restaurant-simulator\backend_final
    python generate_manager_logs.py
"""

import os
import csv
from collections import defaultdict
from modules.synthetic.manager_simulation import (
    generate_restaurant_manager_logs,
    assign_manager_persona,
)


def load_csv(filepath: str) -> list:
    """Load CSV file into list of dicts."""
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return list(reader)


def convert_row(row: dict, is_emotion: bool = True) -> dict:
    """Convert CSV string values to proper types."""
    converted = {}
    
    for key, value in row.items():
        if key in ('restaurant_id', 'day_index', 'mood_emoji'):
            converted[key] = int(value) if value else None
        elif key in ('felt_safe', 'felt_fair', 'felt_respected', 'late', 'callout', 'ncns'):
            converted[key] = value.lower() == 'true' if value else False
        else:
            converted[key] = value
    
    return converted


def main():
    # Paths
    emotions_path = "synthetic_output/daily_emotions.csv"
    behaviors_path = "synthetic_output/daily_behavior.csv"
    output_path = "synthetic_output/manager_logs.csv"
    
    print("=" * 60)
    print("GENERATING SYNTHETIC MANAGER LOGS")
    print("=" * 60)
    
    # Check files exist
    if not os.path.exists(emotions_path):
        print(f"ERROR: {emotions_path} not found")
        print("Run the synthetic simulation first.")
        return
    
    if not os.path.exists(behaviors_path):
        print(f"ERROR: {behaviors_path} not found")
        print("Run the synthetic simulation first.")
        return
    
    # Load data
    print(f"\nLoading {emotions_path}...")
    emotions_raw = load_csv(emotions_path)
    print(f"  Loaded {len(emotions_raw):,} emotion records")
    
    print(f"\nLoading {behaviors_path}...")
    behaviors_raw = load_csv(behaviors_path)
    print(f"  Loaded {len(behaviors_raw):,} behavior records")
    
    # Convert and group by restaurant
    print("\nGrouping by restaurant...")
    emotions_by_restaurant = defaultdict(list)
    for row in emotions_raw:
        converted = convert_row(row, is_emotion=True)
        rid = converted.get('restaurant_id')
        if rid:
            emotions_by_restaurant[rid].append(converted)
    
    behaviors_by_restaurant = defaultdict(list)
    for row in behaviors_raw:
        converted = convert_row(row, is_emotion=False)
        rid = converted.get('restaurant_id')
        if rid:
            behaviors_by_restaurant[rid].append(converted)
    
    restaurant_ids = sorted(emotions_by_restaurant.keys())
    print(f"  Found {len(restaurant_ids)} restaurants")
    
    # Generate manager logs
    print("\nGenerating manager logs...")
    all_logs = []
    
    for i, rid in enumerate(restaurant_ids):
        emotions = emotions_by_restaurant[rid]
        behaviors = behaviors_by_restaurant[rid]
        
        # Get max day to know simulation length
        max_day = max(e.get('day_index', 0) for e in emotions)
        
        logs = generate_restaurant_manager_logs(
            restaurant_id=rid,
            daily_emotions=emotions,
            daily_behaviors=behaviors,
            total_days=max_day,
        )
        
        all_logs.extend(logs)
        
        # Get persona for display
        persona = assign_manager_persona(rid)
        
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  Restaurant {rid}: {persona.persona_type} manager, {len(logs)} logs")
    
    print(f"\nTotal manager logs generated: {len(all_logs):,}")
    
    # Write to CSV
    print(f"\nWriting to {output_path}...")
    
    fieldnames = [
        'restaurant_id',
        'manager_id', 
        'day_index',
        'overall_rating',
        'felt_smooth',
        'felt_understaffed',
        'felt_chaotic',
        'felt_overstaffed',
    ]
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_logs)
    
    print(f"  Wrote {len(all_logs):,} records")
    
    # Summary stats
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    # Count by persona
    persona_counts = defaultdict(int)
    for rid in restaurant_ids:
        persona = assign_manager_persona(rid)
        persona_counts[persona.persona_type] += 1
    
    print("\nManager persona distribution:")
    for persona_type, count in sorted(persona_counts.items()):
        print(f"  {persona_type}: {count} restaurants")
    
    # Rating distribution
    rating_counts = defaultdict(int)
    for log in all_logs:
        rating_counts[log['overall_rating']] += 1
    
    print("\nRating distribution:")
    for rating in sorted(rating_counts.keys()):
        count = rating_counts[rating]
        pct = count / len(all_logs) * 100
        print(f"  {rating}: {count:,} ({pct:.1f}%)")
    
    # Boolean rates
    smooth_count = sum(1 for log in all_logs if log['felt_smooth'])
    understaffed_count = sum(1 for log in all_logs if log['felt_understaffed'])
    chaotic_count = sum(1 for log in all_logs if log['felt_chaotic'])
    overstaffed_count = sum(1 for log in all_logs if log['felt_overstaffed'])
    
    print("\nBoolean perception rates:")
    print(f"  felt_smooth: {smooth_count / len(all_logs) * 100:.1f}%")
    print(f"  felt_understaffed: {understaffed_count / len(all_logs) * 100:.1f}%")
    print(f"  felt_chaotic: {chaotic_count / len(all_logs) * 100:.1f}%")
    print(f"  felt_overstaffed: {overstaffed_count / len(all_logs) * 100:.1f}%")
    
    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"\nNext steps:")
    print(f"1. Run SQL: synthetic_manager_logs.sql in Supabase")
    print(f"2. Upload {output_path} to synthetic_manager_logs table")


if __name__ == "__main__":
    main()