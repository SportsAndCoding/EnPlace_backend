"""
modules/nightly_pipeline/release_commissions.py

Releases held commissions after 7-day refund window.
Transfers funds to sales reps via Stripe Connect.

Runs nightly to:
1. Find commissions where status='held' and release_at <= now
2. Verify rep has completed Stripe Connect onboarding
3. Transfer commission amount to rep's connected account
4. Update commission status to 'released' with stripe_transfer_id
"""

import os
import stripe
from datetime import datetime
from typing import Dict, Any

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")


def release_held_commissions(supabase_client) -> Dict[str, Any]:
    """
    Process all commissions ready for release.
    
    Returns:
        Dict with stats: {
            "processed": N,
            "released": N,
            "skipped_no_connect": N,
            "skipped_not_enabled": N,
            "failed": N,
            "total_amount": float
        }
    """
    stats = {
        "processed": 0,
        "released": 0,
        "skipped_no_connect": 0,
        "skipped_not_enabled": 0,
        "failed": 0,
        "total_amount": 0.0,
    }
    
    now = datetime.utcnow()
    
    # Step 1: Get all held commissions ready for release
    result = supabase_client.table("sales_commissions") \
        .select("id, rep_id, amount, deal_id") \
        .eq("status", "held") \
        .lte("release_at", now.isoformat()) \
        .execute()
    
    commissions = result.data or []
    stats["processed"] = len(commissions)
    
    if not commissions:
        print("    No commissions ready for release")
        return stats
    
    # Step 2: Process each commission
    for commission in commissions:
        commission_id = commission["id"]
        rep_id = commission["rep_id"]
        amount = float(commission["amount"])
        amount_cents = int(amount * 100)
        
        print(f"    Processing commission {commission_id}: ${amount:.2f} for rep {rep_id}")
        
        # Get rep's Stripe Connect account
        rep_result = supabase_client.table("staff") \
            .select("stripe_connect_account_id, full_name") \
            .eq("staff_id", rep_id) \
            .single() \
            .execute()
        
        if not rep_result.data:
            print(f"        SKIP: Rep {rep_id} not found")
            stats["failed"] += 1
            continue
        
        connect_account_id = rep_result.data.get("stripe_connect_account_id")
        rep_name = rep_result.data.get("full_name", rep_id)
        
        if not connect_account_id:
            print(f"        SKIP: {rep_name} has no Stripe Connect account")
            _mark_commission_pending(supabase_client, commission_id, "no_connect_account")
            stats["skipped_no_connect"] += 1
            continue
        
        # Verify account can receive payouts
        try:
            account = stripe.Account.retrieve(connect_account_id)
            if not account.payouts_enabled:
                print(f"        SKIP: {rep_name}'s account not enabled for payouts")
                _mark_commission_pending(supabase_client, commission_id, "payouts_not_enabled")
                stats["skipped_not_enabled"] += 1
                continue
        except stripe.error.StripeError as e:
            print(f"        ERROR: Could not verify account: {e}")
            stats["failed"] += 1
            continue
        
        # Step 3: Create transfer
        try:
            transfer = stripe.Transfer.create(
                amount=amount_cents,
                currency="usd",
                destination=connect_account_id,
                description=f"En Place commission - Deal {commission.get('deal_id', 'N/A')}",
                metadata={
                    "commission_id": str(commission_id),
                    "rep_id": rep_id,
                    "deal_id": str(commission.get("deal_id", "")),
                }
            )
            
            # Step 4: Update commission record
            supabase_client.table("sales_commissions") \
                .update({
                    "status": "released",
                    "stripe_transfer_id": transfer.id,
                    "paid_at": now.isoformat(),
                }) \
                .eq("id", commission_id) \
                .execute()
            
            print(f"        SUCCESS: Transferred ${amount:.2f} to {rep_name} ({transfer.id})")
            stats["released"] += 1
            stats["total_amount"] += amount
            
        except stripe.error.StripeError as e:
            print(f"        ERROR: Transfer failed: {e}")
            _mark_commission_failed(supabase_client, commission_id, str(e))
            stats["failed"] += 1
            continue
    
    return stats


def _mark_commission_pending(supabase_client, commission_id: str, reason: str):
    """
    Move commission back to pending if rep isn't ready to receive.
    They'll be processed again once onboarded.
    """
    supabase_client.table("sales_commissions") \
        .update({
            "status": "pending",
            # Could add a notes field if you want to track reason
        }) \
        .eq("id", commission_id) \
        .execute()


def _mark_commission_failed(supabase_client, commission_id: str, error: str):
    """
    Mark commission as failed for manual review.
    """
    supabase_client.table("sales_commissions") \
        .update({
            "status": "failed",
            # Could store error in a notes field if desired
        }) \
        .eq("id", commission_id) \
        .execute()


def run():
    """
    Entry point for Heroku Scheduler.
    
    Schedule with:
        python modules/nightly_pipeline/release_commissions.py
    """
    from dotenv import load_dotenv
    from supabase import create_client
    
    load_dotenv()
    
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    
    if not url or not key:
        print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        return
    
    if not stripe.api_key:
        print("ERROR: Missing STRIPE_SECRET_KEY")
        return
    
    client = create_client(url, key)
    
    print("=" * 50)
    print("COMMISSION RELEASE JOB")
    print("=" * 50)
    print(f"Running at {datetime.utcnow().isoformat()} UTC")
    print("")
    
    stats = release_held_commissions(client)
    
    print("")
    print("-" * 50)
    print(f"Processed:           {stats['processed']}")
    print(f"Released:            {stats['released']}")
    print(f"Skipped (no acct):   {stats['skipped_no_connect']}")
    print(f"Skipped (not ready): {stats['skipped_not_enabled']}")
    print(f"Failed:              {stats['failed']}")
    print(f"Total transferred:   ${stats['total_amount']:.2f}")
    print("=" * 50)
    print("COMPLETE")


if __name__ == "__main__":
    run()