import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import csv
from typing import Dict, Any, List

from modules.synthetic.restaurant_profiles import get_profile, list_profile_keys
from modules.synthetic.restaurant_simulation_runner import simulate_restaurant


# -------------------------------------------------------------
# 1. CONFIGURATION
# -------------------------------------------------------------

RESTAURANTS_TO_SIMULATE = [
    (101, "steakhouse", 50, 365),
    (102, "sports_bar", 60, 365),
    (103, "fast_casual", 40, 365),
    (104, "neighborhood_bistro", 35, 365),
    (105, "upscale_casual", 55, 365),
    (106, "family_diner", 30, 365),
    (107, "breakfast_cafe", 28, 365),
    (108, "bar_and_grille", 65, 365),
    (109, "high_volume_chain", 75, 365),
    (110, "college_town_cafe", 38, 365),
    (111, "hotel_restaurant", 52, 365),
    (112, "airport_restaurant", 70, 365),
    (113, "steakhouse", 45, 365),
    (114, "sports_bar", 62, 365),
    (115, "fast_casual", 42, 365),
    (116, "neighborhood_bistro", 33, 365),
    (117, "upscale_casual", 58, 365),
    (118, "family_diner", 27, 365),
    (119, "breakfast_cafe", 25, 365),
    (120, "bar_and_grille", 63, 365),
    (121, "high_volume_chain", 78, 365),
    (122, "college_town_cafe", 41, 365),
    (123, "hotel_restaurant", 55, 365),
    (124, "airport_restaurant", 72, 365),
    (125, "steakhouse", 48, 365),
    (126, "sports_bar", 59, 365),
    (127, "fast_casual", 44, 365),
    (128, "neighborhood_bistro", 34, 365),
    (129, "upscale_casual", 53, 365),
    (130, "family_diner", 29, 365),
    (131, "breakfast_cafe", 26, 365),
    (132, "bar_and_grille", 60, 365),
    (133, "high_volume_chain", 80, 365),
    (134, "college_town_cafe", 43, 365),
    (135, "hotel_restaurant", 57, 365),
    (136, "airport_restaurant", 68, 365),
    (137, "steakhouse", 47, 365),
    (138, "sports_bar", 61, 365),
    (139, "fast_casual", 39, 365),
    (140, "neighborhood_bistro", 37, 365),
    (141, "upscale_casual", 54, 365),
    (142, "family_diner", 31, 365),
    (143, "breakfast_cafe", 29, 365),
    (144, "bar_and_grille", 66, 365),
    (145, "high_volume_chain", 76, 365),
    (146, "college_town_cafe", 36, 365),
    (147, "hotel_restaurant", 50, 365),
    (148, "airport_restaurant", 74, 365),
    (149, "steakhouse", 55, 365),
    (150, "sports_bar", 64, 365),
    (151, "fast_casual", 41, 365),
    (152, "neighborhood_bistro", 32, 365),
    (153, "upscale_casual", 56, 365),
    (154, "family_diner", 33, 365),
    (155, "breakfast_cafe", 27, 365),
    (156, "bar_and_grille", 67, 365),
    (157, "high_volume_chain", 73, 365),
    (158, "college_town_cafe", 40, 365),
    (159, "hotel_restaurant", 53, 365),
    (160, "airport_restaurant", 69, 365),
    (161, "steakhouse", 49, 365),
    (162, "sports_bar", 60, 365),
    (163, "fast_casual", 46, 365),
    (164, "neighborhood_bistro", 35, 365),
    (165, "upscale_casual", 59, 365),
    (166, "family_diner", 28, 365),
    (167, "breakfast_cafe", 24, 365),
    (168, "bar_and_grille", 62, 365),
    (169, "high_volume_chain", 77, 365),
    (170, "college_town_cafe", 39, 365),
    (171, "hotel_restaurant", 51, 365),
    (172, "airport_restaurant", 75, 365),
    (173, "steakhouse", 52, 365),
    (174, "sports_bar", 63, 365),
    (175, "fast_casual", 43, 365),
    (176, "neighborhood_bistro", 36, 365),
    (177, "upscale_casual", 57, 365),
    (178, "family_diner", 30, 365),
    (179, "breakfast_cafe", 26, 365),
    (180, "bar_and_grille", 64, 365),
    (181, "high_volume_chain", 79, 365),
    (182, "college_town_cafe", 37, 365),
    (183, "hotel_restaurant", 54, 365),
    (184, "airport_restaurant", 71, 365),
    (185, "steakhouse", 46, 365),
    (186, "sports_bar", 58, 365),
    (187, "fast_casual", 45, 365),
    (188, "neighborhood_bistro", 34, 365),
    (189, "upscale_casual", 52, 365),
    (190, "family_diner", 32, 365),
    (191, "breakfast_cafe", 25, 365),
    (192, "bar_and_grille", 61, 365),
    (193, "high_volume_chain", 74, 365),
    (194, "college_town_cafe", 42, 365),
    (195, "hotel_restaurant", 56, 365),
    (196, "airport_restaurant", 73, 365),
    (197, "steakhouse", 53, 365),
    (198, "sports_bar", 65, 365),
    (199, "fast_casual", 47, 365),
    (200, "neighborhood_bistro", 38, 365)
]


DEFAULT_PERSONA_WEIGHTS = {
    "enthusiastic_rookie": 0.25,
    "lazy_rookie": 0.14,
    "snarky_rookie": 0.15,
    "overwhelmed_rookie": 0.10,
    "workhorse": 0.15,
    "social_glue": 0.05,
    "ghoster_in_training": 0.05,
    "burned_idealist": 0.05,
    "emerging_leader": 0.03,
    "quiet_pro": 0.01,
    "cynical_anchor": 0.01,
    "flight_risk_veteran": 0.01,
}

OUTPUT_DIR = "synthetic_output"
WRITE_CSV = True  # flip to False to skip file writing


# -------------------------------------------------------------
# 2. CSV EXPORT HELPERS
# -------------------------------------------------------------

def ensure_output_dir():
    if WRITE_CSV and not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)


def write_csv(filename: str, rows: List[Dict[str, Any]]):
    if not WRITE_CSV:
        return

    path = os.path.join(OUTPUT_DIR, filename)
    if not rows:
        print(f"[WARN] No rows for {filename}")
        return

    # Determine if header should be written (true when file is empty)
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0

    # Append mode (so multiple restaurants stream into same file)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        if write_header:
            writer.writeheader()
        writer.writerows(rows)

    print(f"[CSV] Wrote {len(rows):,} rows → {path}")


# -------------------------------------------------------------
# 3. MAIN PIPELINE
# -------------------------------------------------------------

def run_full_simulation():
    ensure_output_dir()

    # Truncate CSVs for a fresh run
    for filename in ["staff_master.csv", "daily_emotions.csv", "daily_behavior.csv"]:
        open(os.path.join(OUTPUT_DIR, filename), "w").close()

    combined_staff_master = []
    combined_daily_emotions = []
    combined_daily_behavior = []

    

    for restaurant_id, profile_key, num_staff, num_days in RESTAURANTS_TO_SIMULATE:
        print(f"\n=== Simulating Restaurant {restaurant_id} ({profile_key}) ===")

        profile = get_profile(profile_key)

        results = simulate_restaurant(
            restaurant_id=restaurant_id,
            number_of_staff=num_staff,
            simulation_days=num_days,
            persona_weights=DEFAULT_PERSONA_WEIGHTS,
            restaurant_profile=profile,
        )

        combined_staff_master.extend(results["staff_master"])
        combined_daily_emotions.extend(results["daily_emotions"])
        combined_daily_behavior.extend(results["daily_behavior"])

        print(f"Completed restaurant {restaurant_id}.")

    # ---------------------------------------------------------
    # 4. OPTIONAL CSV EXPORT
    # ---------------------------------------------------------
    write_csv("staff_master.csv", combined_staff_master)
    write_csv("daily_emotions.csv", combined_daily_emotions)
    write_csv("daily_behavior.csv", combined_daily_behavior)

    print("\n=== ALL SIMULATIONS COMPLETE ===")
    print(
        f"Total staff: {len(combined_staff_master):,}\n"
        f"Total emotion rows: {len(combined_daily_emotions):,}\n"
        f"Total behavior rows: {len(combined_daily_behavior):,}\n"
    )


# -------------------------------------------------------------
# 5. ENTRY POINT
# -------------------------------------------------------------

if __name__ == "__main__":
    run_full_simulation()
