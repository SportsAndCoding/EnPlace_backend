"""
calibrate_rates.py

Stop guessing. Binary search for the exact daily quit rates
that produce industry-standard rolling 365-day turnover.

For each restaurant type:
  1. Set a TARGET pre-EP annual turnover from the research
  2. Fix the cliff:established ratio at 5:1
  3. Binary search for the cliff rate that hits the target
  4. Output the exact 2 numbers (cliff, established)

Uses the same simulation mechanics as turnover_analysis.py:
  - N staff, replacement hiring, 912 days
  - Rolling 365-day window measured at pre-EP steady state
"""

import hashlib
import time


def _det_float(seed_str: str) -> float:
    h = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    return (h % 1_000_000) / 1_000_000


def simulate_and_measure(
    headcount: int,
    total_days: int,
    cliff_rate: float,
    estab_rate: float,
    restaurant_seed: int = 7777,
) -> float:
    """
    Run sim with constant rates (no EP switch), measure rolling 365-day
    turnover averaged over the last 365 days (steady state).
    """
    hire_day = [0] * headcount
    gen = [0] * headcount
    exits_per_day = [0] * total_days

    for day in range(total_days):
        for slot in range(headcount):
            tenure = day - hire_day[slot]
            prob = cliff_rate if tenure < 90 else estab_rate
            uid_seed = f"{restaurant_seed}:{slot}:{gen[slot]}:{day}"
            roll = _det_float(uid_seed)
            if roll < prob:
                exits_per_day[day] += 1
                hire_day[slot] = day + 1
                gen[slot] += 1

    # Rolling 365-day turnover, averaged over last 365 points (steady state)
    window = 365
    # Measure from day 547 onward (well past warmup)
    measure_start = 547
    running = sum(exits_per_day[measure_start - window:measure_start])
    values = []
    for day in range(measure_start, total_days):
        running += exits_per_day[day]
        running -= exits_per_day[day - window]
        values.append((running / headcount) * 100)

    return sum(values) / len(values) if values else 0


def find_cliff_rate(
    target_turnover: float,
    headcount: int,
    cliff_estab_ratio: float = 5.0,
    tolerance: float = 2.0,
    max_iterations: int = 20,
) -> tuple:
    """
    Binary search for cliff_rate where:
      cliff_rate = X
      estab_rate = X / cliff_estab_ratio
    produces target_turnover (rolling 365-day %).
    """
    low = 0.001
    high = 0.020
    best_cliff = None
    best_estab = None
    best_result = None

    for i in range(max_iterations):
        mid = (low + high) / 2
        estab = mid / cliff_estab_ratio
        result = simulate_and_measure(headcount, 912, mid, estab)

        if best_result is None or abs(result - target_turnover) < abs(best_result - target_turnover):
            best_cliff = mid
            best_estab = estab
            best_result = result

        if abs(result - target_turnover) <= tolerance:
            break

        if result < target_turnover:
            low = mid
        else:
            high = mid

    return best_cliff, best_estab, best_result


# =====================================================================
# TARGETS FROM THE RESEARCH
# =====================================================================

TARGETS = {
    # type:            (target_turnover%, headcount for calibration)
    "fast_casual":       (130, 45),   # NRA: 100-150%, midpoint ~130
    "high_volume_chain": (100, 76),   # BLS: 100%+
    "college_town_cafe": (90,  38),   # Industry: 90%+ (students)
    "airport_restaurant":(82,  70),   # Industry: 80%+ (transient)
    "sports_bar":        (78,  60),   # Industry: 75-80%
    "bar_and_grille":    (78,  63),   # Industry: 75-80%
    "hotel_restaurant":  (78,  53),   # Industry: 75-80%
    "family_diner":      (78,  30),   # Industry: 75-80%
    "breakfast_cafe":    (78,  26),   # Industry: 75-80%
    "upscale_casual":    (75,  55),   # Industry: 70-80%
    "neighborhood_bistro":(75, 35),   # Industry: 70-80%
    "steakhouse":        (60,  50),   # Fine dining: 50-70%, midpoint ~60
}

# Post-EP targets: the REx Network numbers from the research
# These are what restaurants WITH En Place actually achieve
POST_EP_TARGETS = {
    "fast_casual":        (45, 45),   # 130→45 — the mic drop stat
    "high_volume_chain":  (48, 76),   # 100→48
    "college_town_cafe":  (47, 38),   # 90→47
    "airport_restaurant": (48, 70),   # 82→48
    "sports_bar":         (50, 60),   # 78→50
    "bar_and_grille":     (50, 63),   # 78→50
    "hotel_restaurant":   (48, 53),   # 78→48
    "family_diner":       (45, 30),   # 78→45
    "breakfast_cafe":     (45, 26),   # 78→45
    "upscale_casual":     (48, 55),   # 75→48
    "neighborhood_bistro":(46, 35),   # 75→46
    "steakhouse":         (45, 50),   # 60→45
}


if __name__ == "__main__":

    print("=" * 75)
    print("STEP 1: PRE-EP CALIBRATION (Without En Place = Industry Baseline)")
    print("  Method: Binary search, 912-day sim, rolling 365-day window")
    print("  Cliff:Established ratio = 5:1")
    print("=" * 75)

    print(f"\n  {'Type':<22} {'Target':>7} {'Result':>7} {'Cliff':>10} {'Estab':>10} {'Err':>6}")
    print(f"  {'-'*22} {'-'*7} {'-'*7} {'-'*10} {'-'*10} {'-'*6}")

    start = time.time()

    pre_results = {}
    for pkey, (target, hc) in TARGETS.items():
        cliff, estab, actual = find_cliff_rate(target, hc)
        err = actual - target
        pre_results[pkey] = (cliff, estab, actual)
        print(f"  {pkey:<22} {target:>6.0f}% {actual:>6.1f}% "
              f"{cliff:>10.6f} {estab:>10.6f} {err:>+5.1f}")

    print(f"\n{'='*75}")
    print("STEP 2: POST-EP CALIBRATION (With En Place)")
    print("=" * 75)

    print(f"\n  {'Type':<22} {'Target':>7} {'Result':>7} {'Cliff':>10} {'Estab':>10} {'Err':>6}")
    print(f"  {'-'*22} {'-'*7} {'-'*7} {'-'*10} {'-'*10} {'-'*6}")

    post_results = {}
    for pkey, (target, hc) in POST_EP_TARGETS.items():
        cliff, estab, actual = find_cliff_rate(target, hc)
        err = actual - target
        post_results[pkey] = (cliff, estab, actual)
        print(f"  {pkey:<22} {target:>6.0f}% {actual:>6.1f}% "
              f"{cliff:>10.6f} {estab:>10.6f} {err:>+5.1f}")

    elapsed = time.time() - start
    print(f"\n  Completed in {elapsed:.1f}s")

    # Combined summary
    print(f"\n{'='*75}")
    print("SUMMARY: Pre -> Post by type")
    print(f"{'='*75}")
    print(f"\n  {'Type':<22} {'PreTgt':>7} {'PreAct':>7} {'PostTgt':>8} {'PostAct':>8} {'Delta':>6}")
    print(f"  {'-'*22} {'-'*7} {'-'*7} {'-'*8} {'-'*8} {'-'*6}")
    for pkey in TARGETS:
        pre_tgt = TARGETS[pkey][0]
        pre_act = pre_results[pkey][2]
        post_tgt = POST_EP_TARGETS[pkey][0]
        post_act = post_results[pkey][2]
        delta = pre_act - post_act
        print(f"  {pkey:<22} {pre_tgt:>6.0f}% {pre_act:>6.1f}% {post_tgt:>7.0f}% {post_act:>7.1f}% {delta:>+5.0f}")

    # Copy-paste config
    print(f"\n{'='*75}")
    print("COPY-PASTE CONFIG FOR turnover_analysis.py:")
    print(f"{'='*75}\n")

    hc_ranges = {
        "fast_casual": (38, 48), "high_volume_chain": (70, 82),
        "college_town_cafe": (34, 44), "airport_restaurant": (65, 76),
        "sports_bar": (55, 65), "bar_and_grille": (58, 68),
        "hotel_restaurant": (48, 58), "upscale_casual": (50, 60),
        "family_diner": (25, 35), "breakfast_cafe": (22, 30),
        "neighborhood_bistro": (30, 40), "steakhouse": (45, 55),
    }

    for pkey in TARGETS:
        pre_c, pre_e, pre_a = pre_results[pkey]
        post_c, post_e, post_a = post_results[pkey]
        hc_lo, hc_hi = hc_ranges[pkey]
        pre_tgt = TARGETS[pkey][0]
        post_tgt = POST_EP_TARGETS[pkey][0]
        print(f'    "{pkey}": {{')
        print(f'        "cliff_without":  {pre_c:.6f},   # Target: {pre_tgt}%, Actual: {pre_a:.1f}%')
        print(f'        "estab_without":  {pre_e:.6f},')
        print(f'        "cliff_with":     {post_c:.6f},   # Target: {post_tgt}%, Actual: {post_a:.1f}%')
        print(f'        "estab_with":     {post_e:.6f},')
        print(f'        "headcount_range": ({hc_lo}, {hc_hi}),')
        print(f'    }},')