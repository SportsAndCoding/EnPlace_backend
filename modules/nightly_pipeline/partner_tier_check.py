"""
modules/nightly_pipeline/partner_tier_check.py
Partner tier expiry enforcement.

Runs daily. For each partner with status = 'certified':
- If partner_tier_expires_at has passed AND active_referrals = 0,
  lapse them: set status to 'lapsed', plan to 'free'.

Does NOT touch 'active' partners (they have no expiry while
at least one referred restaurant is subscribed).

Usage:
    python modules/nightly_pipeline/partner_tier_check.py

For Heroku Scheduler:
    python modules/nightly_pipeline/partner_tier_check.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import get_supabase


def run_partner_tier_check():
    supabase = get_supabase()
    now = datetime.utcnow()

    print(f"[{now.isoformat()}] Partner tier check — looking for expired certified partners...")

    # Get certified partners whose tier has expired
    result = supabase.table("proof_partners") \
        .select("id, user_id, partner_tier_expires_at, active_referrals") \
        .eq("status", "certified") \
        .not_.is_("partner_tier_expires_at", "null") \
        .lt("partner_tier_expires_at", now.isoformat()) \
        .execute()

    partners = result.data or []
    print(f"  Found {len(partners)} certified partners past expiry date")

    lapsed = 0
    skipped = 0
    errors = 0

    for p in partners:
        partner_id = p["id"]
        user_id = p["user_id"]
        active_refs = p.get("active_referrals", 0) or 0

        # Safety check: if they somehow have active referrals, don't lapse
        if active_refs > 0:
            print(f"  SKIP partner {partner_id}: has {active_refs} active referrals despite certified status")
            skipped += 1
            continue

        try:
            # Lapse the partner
            supabase.table("proof_partners") \
                .update({
                    "status": "lapsed",
                    "updated_at": now.isoformat()
                }) \
                .eq("id", partner_id) \
                .execute()

            # Revert plan to free
            supabase.table("proof_users") \
                .update({
                    "plan": "free",
                    "plan_status": "active"
                }) \
                .eq("id", user_id) \
                .execute()

            lapsed += 1
            print(f"  LAPSED partner {partner_id} (expired: {p['partner_tier_expires_at']})")

        except Exception as e:
            errors += 1
            print(f"  ERROR partner {partner_id}: {e}")

    print(f"  Done. Lapsed: {lapsed}, Skipped: {skipped}, Errors: {errors}")


if __name__ == "__main__":
    run_partner_tier_check()
