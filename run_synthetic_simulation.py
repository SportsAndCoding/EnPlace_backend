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
    (101, "steakhouse", 50, 180),
    (102, "sports_bar", 60, 180),
    (103, "fast_casual", 40, 180),
    (104, "neighborhood_bistro", 35, 180),
    (105, "upscale_casual", 55, 180),
    (106, "family_diner", 30, 180),
    (107, "breakfast_cafe", 28, 180),
    (108, "bar_and_grille", 65, 180),
    (109, "high_volume_chain", 75, 180),
    (110, "college_town_cafe", 38, 180),
    (111, "hotel_restaurant", 52, 180),
    (112, "airport_restaurant", 70, 180),
    (113, "steakhouse", 45, 180),
    (114, "sports_bar", 62, 180),
    (115, "fast_casual", 42, 180),
    (116, "neighborhood_bistro", 33, 180),
    (117, "upscale_casual", 58, 180),
    (118, "family_diner", 27, 180),
    (119, "breakfast_cafe", 25, 180),
    (120, "bar_and_grille", 63, 180),
    (121, "high_volume_chain", 78, 180),
    (122, "college_town_cafe", 41, 180),
    (123, "hotel_restaurant", 55, 180),
    (124, "airport_restaurant", 72, 180),
    (125, "steakhouse", 48, 180),
    (126, "sports_bar", 59, 180),
    (127, "fast_casual", 44, 180),
    (128, "neighborhood_bistro", 34, 180),
    (129, "upscale_casual", 53, 180),
    (130, "family_diner", 29, 180),
    (131, "breakfast_cafe", 26, 180),
    (132, "bar_and_grille", 60, 180),
    (133, "high_volume_chain", 80, 180),
    (134, "college_town_cafe", 43, 180),
    (135, "hotel_restaurant", 57, 180),
    (136, "airport_restaurant", 68, 180),
    (137, "steakhouse", 47, 180),
    (138, "sports_bar", 61, 180),
    (139, "fast_casual", 39, 180),
    (140, "neighborhood_bistro", 37, 180),
    (141, "upscale_casual", 54, 180),
    (142, "family_diner", 31, 180),
    (143, "breakfast_cafe", 29, 180),
    (144, "bar_and_grille", 66, 180),
    (145, "high_volume_chain", 76, 180),
    (146, "college_town_cafe", 36, 180),
    (147, "hotel_restaurant", 50, 180),
    (148, "airport_restaurant", 74, 180),
    (149, "steakhouse", 55, 180),
    (150, "sports_bar", 64, 180),
    (151, "fast_casual", 41, 180),
    (152, "neighborhood_bistro", 32, 180),
    (153, "upscale_casual", 56, 180),
    (154, "family_diner", 33, 180),
    (155, "breakfast_cafe", 27, 180),
    (156, "bar_and_grille", 67, 180),
    (157, "high_volume_chain", 73, 180),
    (158, "college_town_cafe", 40, 180),
    (159, "hotel_restaurant", 53, 180),
    (160, "airport_restaurant", 69, 180),
    (161, "steakhouse", 49, 180),
    (162, "sports_bar", 60, 180),
    (163, "fast_casual", 46, 180),
    (164, "neighborhood_bistro", 35, 180),
    (165, "upscale_casual", 59, 180),
    (166, "family_diner", 28, 180),
    (167, "breakfast_cafe", 24, 180),
    (168, "bar_and_grille", 62, 180),
    (169, "high_volume_chain", 77, 180),
    (170, "college_town_cafe", 39, 180),
    (171, "hotel_restaurant", 51, 180),
    (172, "airport_restaurant", 75, 180),
    (173, "steakhouse", 52, 180),
    (174, "sports_bar", 63, 180),
    (175, "fast_casual", 43, 180),
    (176, "neighborhood_bistro", 36, 180),
    (177, "upscale_casual", 57, 180),
    (178, "family_diner", 30, 180),
    (179, "breakfast_cafe", 26, 180),
    (180, "bar_and_grille", 64, 180),
    (181, "high_volume_chain", 79, 180),
    (182, "college_town_cafe", 37, 180),
    (183, "hotel_restaurant", 54, 180),
    (184, "airport_restaurant", 71, 180),
    (185, "steakhouse", 46, 180),
    (186, "sports_bar", 58, 180),
    (187, "fast_casual", 45, 180),
    (188, "neighborhood_bistro", 34, 180),
    (189, "upscale_casual", 52, 180),
    (190, "family_diner", 32, 180),
    (191, "breakfast_cafe", 25, 180),
    (192, "bar_and_grille", 61, 180),
    (193, "high_volume_chain", 74, 180),
    (194, "college_town_cafe", 42, 180),
    (195, "hotel_restaurant", 56, 180),
    (196, "airport_restaurant", 73, 180),
    (197, "steakhouse", 53, 180),
    (198, "sports_bar", 65, 180),
    (199, "fast_casual", 47, 180),
    (200, "neighborhood_bistro", 38, 180)
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
