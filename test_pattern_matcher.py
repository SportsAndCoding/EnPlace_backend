"""
test_pattern_matcher.py

Run the pattern matcher against Supabase to:
1. Extract quitter signatures from synthetic network
2. Print analysis report
3. Save signatures to JSON for caching

Run from backend_final directory:
    python test_pattern_matcher.py
"""

import os
import sys
import json
from dotenv import load_dotenv

# Load environment variables FIRST
load_dotenv()

# Add project root for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supabase import create_client

# Import our pattern matcher
from modules.network_intelligence.pattern_matcher import (
    extract_signatures,
    print_signature_report,
    signatures_to_dict,
    score_staff_flight_risk,
    calculate_network_percentile,
)


def main():
    # Initialize Supabase
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    
    if not url or not key:
        print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
        sys.exit(1)
    
    print("Connecting to Supabase...")
    client = create_client(url, key)
    
    # =========================================================================
    # STEP 1: Extract signatures from synthetic network
    # =========================================================================
    print("\n" + "=" * 70)
    print("STEP 1: EXTRACTING QUITTER SIGNATURES")
    print("=" * 70)
    
    signatures = extract_signatures(client, lookback_days=14)
    
    # Print detailed report
    print_signature_report(signatures)
    
    # Save to JSON for caching
    sig_dict = signatures_to_dict(signatures)
    with open("quitter_signatures.json", "w") as f:
        json.dump(sig_dict, f, indent=2)
    print(f"\nSignatures saved to quitter_signatures.json")
    
    # =========================================================================
    # STEP 2: Test flight risk scoring on Demo Bistro (restaurant_id=1)
    # =========================================================================
    print("\n" + "=" * 70)
    print("STEP 2: SCORING DEMO BISTRO STAFF")
    print("=" * 70)
    
    try:
        flight_scores = score_staff_flight_risk(
            client,
            restaurant_id=1,  # Demo Bistro
            signatures=signatures,
            lookback_days=14,
        )
        
        if not flight_scores:
            print("\nNo staff with enough check-in data to score.")
        else:
            print(f"\nScored {len(flight_scores)} staff members:\n")
            
            for score in flight_scores[:10]:  # Top 10 risk
                print(f"  {score.staff_id[:8]}... | Score: {score.score:3d} | "
                      f"{score.risk_level.upper():10s} | {score.primary_concern}")
                if score.contributing_factors:
                    print(f"                      Factors: {', '.join(score.contributing_factors)}")
    except Exception as e:
        print(f"\nCouldn't score Demo Bistro: {e}")
    
    # =========================================================================
    # STEP 3: Test network benchmark
    # =========================================================================
    print("\n" + "=" * 70)
    print("STEP 3: NETWORK BENCHMARK")
    print("=" * 70)
    
    try:
        for metric in ["mood", "safety", "fairness", "respect"]:
            result = calculate_network_percentile(client, restaurant_id=1, metric=metric)
            
            if result.get("error"):
                print(f"\n  {metric.upper()}: {result['error']}")
            else:
                print(f"\n  {metric.upper()}:")
                print(f"    Restaurant: {result['restaurant_value']}")
                print(f"    Network avg: {result['network_average']}")
                print(f"    Percentile: {result['percentile']}%")
    except Exception as e:
        print(f"\nCouldn't calculate benchmark: {e}")
    
    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()