"""
modules/nightly_pipeline/partner_credit_grant.py
Monthly credit grant for En Place partners.

Runs daily. For each partner with status in ('certified', 'active'):
- If last_credit_grant_at is 30+ days ago, deposit monthly_credit_amount.
- Skips partners with NULL last_credit_grant_at (should not happen —
  certification sets it, but defensive).

Usage:
    python modules/nightly_pipeline/partner_credit_grant.py

For Heroku Scheduler:
    python modules/nightly_pipeline/partner_credit_grant.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv
load_dotenv()

from database.supabase_client import get_supabase


def run_partner_credit_grants():
    supabase = get_supabase()
    now = datetime.utcnow()
    cutoff = (now - timedelta(days=30)).isoformat()

    print(f"[{now.isoformat()}] Partner credit grant — checking eligible partners...")

    # Get all certified/active partners whose last grant was 30+ days ago
    result = supabase.table("proof_partners") \
        .select("id, user_id, monthly_credit_amount, last_credit_grant_at, status") \
        .in_("status", ["certified", "active"]) \
        .not_.is_("last_credit_grant_at", "null") \
        .lt("last_credit_grant_at", cutoff) \
        .execute()

    partners = result.data or []
    print(f"  Found {len(partners)} partners eligible for credit grant")

    granted = 0
    errors = 0

    for p in partners:
        partner_id = p["id"]
        user_id = p["user_id"]
        grant_amount = float(p.get("monthly_credit_amount") or 155.00)

        try:
            # Get current balance
            user = supabase.table("proof_users") \
                .select("credit_balance") \
                .eq("id", user_id) \
                .single() \
                .execute()

            if not user.data:
                print(f"  SKIP partner {partner_id}: user {user_id} not found")
                continue

            current_balance = float(user.data.get("credit_balance", 0))
            new_balance = round(current_balance + grant_amount, 2)

            # Credit the balance
            supabase.table("proof_users") \
                .update({"credit_balance": new_balance}) \
                .eq("id", user_id) \
                .execute()

            # Record the transaction
            supabase.table("proof_credit_transactions").insert({
                "user_id": user_id,
                "transaction_type": "partner_grant",
                "amount": grant_amount,
                "balance_after": new_balance,
                "description": "Monthly partner credit grant",
                "created_at": now.isoformat()
            }).execute()

            # Update last grant timestamp
            supabase.table("proof_partners") \
                .update({
                    "last_credit_grant_at": now.isoformat(),
                    "updated_at": now.isoformat()
                }) \
                .eq("id", partner_id) \
                .execute()

            granted += 1
            print(f"  GRANTED ${grant_amount:.2f} to partner {partner_id} (balance: ${current_balance:.2f} -> ${new_balance:.2f})")

        except Exception as e:
            errors += 1
            print(f"  ERROR partner {partner_id}: {e}")

    print(f"  Done. Granted: {granted}, Errors: {errors}, Skipped: {len(partners) - granted - errors}")


if __name__ == "__main__":
    run_partner_credit_grants()
