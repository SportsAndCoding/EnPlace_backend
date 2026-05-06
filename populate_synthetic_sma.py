"""
Populate sma_score column in synthetic_restaurants table.
Computes SMA (Staff-Manager Alignment) for each synthetic restaurant.

Run: python populate_synthetic_sma.py
"""

from database.supabase_client import supabase


def compute_sma_scores_by_restaurant() -> dict:
    """
    Compute SMA score for each synthetic restaurant.
    Returns dict of {organization_id: sma_score}
    """
    
    # Get max day_index for recent data
    max_day_result = supabase.table("synthetic_daily_emotions") \
        .select("day_index") \
        .order("day_index", desc=True) \
        .limit(1) \
        .execute()

    if not max_day_result.data:
        print("No emotion data found")
        return {}

    max_day = max_day_result.data[0]["day_index"]
    recent_start = max_day - 7
    print(f"Using day_index range: {recent_start} to {max_day}")

    # Get staff emotions with pagination
    print("Fetching staff emotions...")
    all_emotions = []
    offset = 0
    batch_size = 1000

    while True:
        emotions_result = supabase.table("synthetic_daily_emotions") \
            .select("organization_id, day_index, mood_emoji") \
            .gte("day_index", recent_start) \
            .range(offset, offset + batch_size - 1) \
            .execute()

        if not emotions_result.data:
            break

        all_emotions.extend(emotions_result.data)

        if len(emotions_result.data) < batch_size:
            break

        offset += batch_size

    print(f"Fetched {len(all_emotions)} emotion records")

    if not all_emotions:
        return {}

    # Get manager logs
    print("Fetching manager logs...")
    manager_result = supabase.table("synthetic_manager_logs") \
        .select("organization_id, day_index, overall_rating") \
        .gte("day_index", recent_start) \
        .execute()

    if not manager_result.data:
        print("No manager logs found")
        return {}

    print(f"Fetched {len(manager_result.data)} manager log records")

    # Aggregate staff mood by restaurant+day
    staff_by_day = {}
    for row in all_emotions:
        rid = row["organization_id"]
        day = row["day_index"]
        key = (rid, day)

        if key not in staff_by_day:
            staff_by_day[key] = []

        if row.get("mood_emoji") is not None:
            staff_by_day[key].append(row["mood_emoji"])

    # Index manager ratings by restaurant+day
    manager_by_day = {}
    for row in manager_result.data:
        rid = row["organization_id"]
        day = row["day_index"]
        key = (rid, day)
        manager_by_day[key] = row.get("overall_rating")

    # Calculate SMA per restaurant
    restaurant_alignments = {}

    for (rid, day), moods in staff_by_day.items():
        if not moods:
            continue

        staff_avg = sum(moods) / len(moods)
        manager_rating = manager_by_day.get((rid, day))

        if manager_rating is None:
            continue

        if rid not in restaurant_alignments:
            restaurant_alignments[rid] = {"aligned": 0, "total": 0}

        restaurant_alignments[rid]["total"] += 1

        # Aligned if within 1 point
        if abs(staff_avg - manager_rating) <= 1.0:
            restaurant_alignments[rid]["aligned"] += 1

    # Compute SMA score (0-100) for each restaurant
    scores = {}
    for rid, data in restaurant_alignments.items():
        if data["total"] > 0:
            alignment_rate = data["aligned"] / data["total"]
            sma_score = int(round(alignment_rate * 100))
            scores[rid] = sma_score

    return scores


def populate_sma_scores():
    """Main function to compute and store SMA scores."""
    
    print("=" * 60)
    print("SYNTHETIC SMA SCORE POPULATION")
    print("=" * 60)
    
    # Compute scores
    scores = compute_sma_scores_by_restaurant()
    
    if not scores:
        print("No scores computed. Exiting.")
        return
    
    print(f"\nComputed SMA scores for {len(scores)} restaurants")
    print(f"Score range: {min(scores.values())} - {max(scores.values())}")
    print(f"Average: {sum(scores.values()) / len(scores):.1f}")
    
    # Update database
    print("\nUpdating synthetic_restaurants table...")
    
    updated = 0
    errors = 0
    
    for organization_id, sma_score in scores.items():
        try:
            supabase.table("synthetic_organizations") \
                .update({"sma_score": sma_score}) \
                .eq("organization_id", organization_id) \
                .execute()
            updated += 1
        except Exception as e:
            print(f"Error updating restaurant {organization_id}: {e}")
            errors += 1
    
    print(f"\nComplete!")
    print(f"Updated: {updated}")
    print(f"Errors: {errors}")
    
    # Verify
    print("\nVerifying stored scores...")
    verify_result = supabase.table("synthetic_organizations") \
        .select("organization_id, sma_score") \
        .not_.is_("sma_score", "null") \
        .limit(10) \
        .execute()
    
    if verify_result.data:
        print("Sample stored scores:")
        for row in verify_result.data[:5]:
            print(f"  Restaurant {row['organization_id']}: {row['sma_score']}")
    
    # Count populated
    count_result = supabase.table("synthetic_organizations") \
        .select("organization_id", count="exact") \
        .not_.is_("sma_score", "null") \
        .execute()
    
    print(f"\nTotal restaurants with sma_score: {count_result.count}")


if __name__ == "__main__":
    populate_sma_scores()