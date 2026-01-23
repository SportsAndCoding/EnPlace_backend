# routes/admin.py
"""
Admin Routes
============
Financial dashboard - invoices, expenses, cash flow.
Restricted to founder_ceo portal_access.
"""

import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from database.supabase_client import get_supabase
from services.auth_service import verify_jwt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH HELPER
# ═══════════════════════════════════════════════════════════════════════════════

async def verify_admin(current_staff: dict = Depends(verify_jwt_token)):
    """Verify user has admin access (founder_ceo)"""
    if current_staff.get("portal_access") != "founder_ceo":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_staff


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ExpenseCreate(BaseModel):
    amount_cents: int
    recipient: str
    description: Optional[str] = None
    expense_date: str  # YYYY-MM-DD format


class ExpenseResponse(BaseModel):
    id: str
    amount_cents: int
    currency: str
    recipient: str
    description: Optional[str]
    expense_date: str
    created_at: str


class DashboardStats(BaseModel):
    total_revenue_cents: int
    total_expenses_cents: int
    net_cents: int
    invoice_count: int
    expense_count: int
    period_start: str
    period_end: str


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def get_dashboard(
    days: int = 30,
    current_staff: dict = Depends(verify_admin)
):
    """
    Get financial dashboard summary.
    Default: last 30 days.
    """
    supabase = get_supabase()
    
    try:
        period_end = datetime.utcnow()
        period_start = period_end - timedelta(days=days)
        
        # Get invoices (revenue)
        invoices_result = supabase.table("invoices") \
            .select("amount_cents, status") \
            .gte("paid_at", period_start.isoformat()) \
            .lte("paid_at", period_end.isoformat()) \
            .eq("status", "paid") \
            .execute()
        
        total_revenue = sum(inv["amount_cents"] for inv in invoices_result.data) if invoices_result.data else 0
        invoice_count = len(invoices_result.data) if invoices_result.data else 0
        
        # Get expenses
        expenses_result = supabase.table("expenses") \
            .select("amount_cents") \
            .gte("expense_date", period_start.date().isoformat()) \
            .lte("expense_date", period_end.date().isoformat()) \
            .execute()
        
        total_expenses = sum(exp["amount_cents"] for exp in expenses_result.data) if expenses_result.data else 0
        expense_count = len(expenses_result.data) if expenses_result.data else 0
        
        return {
            "success": True,
            "total_revenue_cents": total_revenue,
            "total_expenses_cents": total_expenses,
            "net_cents": total_revenue - total_expenses,
            "invoice_count": invoice_count,
            "expense_count": expense_count,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat()
        }
    
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail="Failed to load dashboard")


# ═══════════════════════════════════════════════════════════════════════════════
# INVOICES (Revenue)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/invoices")
async def list_invoices(
    limit: int = 50,
    offset: int = 0,
    current_staff: dict = Depends(verify_admin)
):
    """List all invoices (revenue records)"""
    supabase = get_supabase()
    
    try:
        result = supabase.table("invoices") \
            .select("*, restaurants(name)") \
            .order("paid_at", desc=True) \
            .range(offset, offset + limit - 1) \
            .execute()
        
        return {
            "success": True,
            "invoices": result.data,
            "count": len(result.data)
        }
    
    except Exception as e:
        logger.error(f"List invoices error: {e}")
        raise HTTPException(status_code=500, detail="Failed to load invoices")


# ═══════════════════════════════════════════════════════════════════════════════
# EXPENSES
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/expenses")
async def list_expenses(
    limit: int = 50,
    offset: int = 0,
    current_staff: dict = Depends(verify_admin)
):
    """List all expenses"""
    supabase = get_supabase()
    
    try:
        result = supabase.table("expenses") \
            .select("*") \
            .order("expense_date", desc=True) \
            .range(offset, offset + limit - 1) \
            .execute()
        
        return {
            "success": True,
            "expenses": result.data,
            "count": len(result.data)
        }
    
    except Exception as e:
        logger.error(f"List expenses error: {e}")
        raise HTTPException(status_code=500, detail="Failed to load expenses")


@router.post("/expenses")
async def create_expense(
    expense: ExpenseCreate,
    current_staff: dict = Depends(verify_admin)
):
    """Log a new expense"""
    supabase = get_supabase()
    
    try:
        result = supabase.table("expenses").insert({
            "amount_cents": expense.amount_cents,
            "recipient": expense.recipient,
            "description": expense.description,
            "expense_date": expense.expense_date,
            "currency": "usd"
        }).execute()
        
        logger.info(f"Expense logged: ${expense.amount_cents / 100:.2f} to {expense.recipient}")
        
        return {
            "success": True,
            "expense": result.data[0] if result.data else None
        }
    
    except Exception as e:
        logger.error(f"Create expense error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create expense")


@router.delete("/expenses/{expense_id}")
async def delete_expense(
    expense_id: str,
    current_staff: dict = Depends(verify_admin)
):
    """Delete an expense"""
    supabase = get_supabase()
    
    try:
        supabase.table("expenses") \
            .delete() \
            .eq("id", expense_id) \
            .execute()
        
        return {"success": True, "message": "Expense deleted"}
    
    except Exception as e:
        logger.error(f"Delete expense error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete expense")
    
# ═══════════════════════════════════════════════════════════════════════════════
# COMMISSION APPROVAL
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/commissions/pending")
async def get_pending_commissions(current_staff: dict = Depends(verify_admin)):
    """
    Get all commissions pending approval.
    These are commissions where the 7-day hold has passed and they're ready for review.
    """
    supabase = get_supabase()
    
    try:
        result = supabase.table("sales_commissions") \
            .select("*, sales_deals(monthly_value, restaurant_id, restaurants(name))") \
            .eq("status", "pending_approval") \
            .order("created_at", desc=False) \
            .execute()
        
        commissions = result.data or []
        
        # Enrich with rep info
        enriched = []
        for c in commissions:
            # Get rep name
            rep_result = supabase.table("staff") \
                .select("full_name, email, stripe_connect_account_id") \
                .eq("staff_id", c["rep_id"]) \
                .single() \
                .execute()
            
            rep_data = rep_result.data if rep_result.data else {}
            
            enriched.append({
                **c,
                "rep_name": rep_data.get("full_name", c["rep_id"]),
                "rep_email": rep_data.get("email"),
                "rep_has_stripe": bool(rep_data.get("stripe_connect_account_id")),
                "restaurant_name": c.get("sales_deals", {}).get("restaurants", {}).get("name") if c.get("sales_deals") else None
            })
        
        return {
            "success": True,
            "commissions": enriched,
            "count": len(enriched)
        }
    
    except Exception as e:
        logger.error(f"Error fetching pending commissions: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch commissions")


@router.post("/commissions/{commission_id}/approve")
async def approve_commission(commission_id: str, current_staff: dict = Depends(verify_admin)):
    """
    Approve a commission and trigger Stripe transfer.
    """
    import os
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    
    supabase = get_supabase()
    
    try:
        # Get commission
        commission_result = supabase.table("sales_commissions") \
            .select("*") \
            .eq("id", commission_id) \
            .single() \
            .execute()
        
        if not commission_result.data:
            raise HTTPException(status_code=404, detail="Commission not found")
        
        commission = commission_result.data
        
        if commission["status"] != "pending_approval":
            raise HTTPException(status_code=400, detail=f"Commission is {commission['status']}, not pending_approval")
        
        # Get rep's Stripe Connect account
        rep_result = supabase.table("staff") \
            .select("stripe_connect_account_id, full_name") \
            .eq("staff_id", commission["rep_id"]) \
            .single() \
            .execute()
        
        if not rep_result.data:
            raise HTTPException(status_code=404, detail="Rep not found")
        
        connect_account_id = rep_result.data.get("stripe_connect_account_id")
        rep_name = rep_result.data.get("full_name", commission["rep_id"])
        
        if not connect_account_id:
            raise HTTPException(status_code=400, detail=f"{rep_name} has not set up their Stripe account yet")
        
        # Verify account can receive payouts
        try:
            account = stripe.Account.retrieve(connect_account_id)
            if not account.payouts_enabled:
                raise HTTPException(status_code=400, detail=f"{rep_name}'s Stripe account is not enabled for payouts")
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=f"Stripe error: {str(e)}")
        
        # Create transfer
        amount_cents = int(float(commission["amount"]) * 100)
        
        try:
            transfer = stripe.Transfer.create(
                amount=amount_cents,
                currency="usd",
                destination=connect_account_id,
                description=f"En Place commission - Deal {commission.get('deal_id', 'N/A')}",
                metadata={
                    "commission_id": str(commission_id),
                    "rep_id": commission["rep_id"],
                    "approved_by": current_staff.get("staff_id")
                }
            )
            
            # Update commission
            supabase.table("sales_commissions") \
                .update({
                    "status": "released",
                    "stripe_transfer_id": transfer.id,
                    "paid_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", commission_id) \
                .execute()
            
            logger.info(f"Commission {commission_id} approved by {current_staff.get('staff_id')}: ${commission['amount']} to {rep_name}")
            
            return {
                "success": True,
                "message": f"Transferred ${commission['amount']:.2f} to {rep_name}",
                "transfer_id": transfer.id
            }
        
        except stripe.error.StripeError as e:
            logger.error(f"Transfer failed for commission {commission_id}: {e}")
            raise HTTPException(status_code=400, detail=f"Transfer failed: {str(e)}")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving commission {commission_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to approve commission")


@router.post("/commissions/{commission_id}/reject")
async def reject_commission(commission_id: str, reason: str = None, current_staff: dict = Depends(verify_admin)):
    """
    Reject a commission (won't be paid).
    Use for duplicate entries, cancelled deals, etc.
    """
    supabase = get_supabase()
    
    try:
        # Get commission
        commission_result = supabase.table("sales_commissions") \
            .select("*") \
            .eq("id", commission_id) \
            .single() \
            .execute()
        
        if not commission_result.data:
            raise HTTPException(status_code=404, detail="Commission not found")
        
        commission = commission_result.data
        
        if commission["status"] not in ["pending_approval", "held", "pending"]:
            raise HTTPException(status_code=400, detail=f"Cannot reject commission with status {commission['status']}")
        
        # Update to rejected
        supabase.table("sales_commissions") \
            .update({
                "status": "rejected",
                # Could add a rejection_reason field if needed
            }) \
            .eq("id", commission_id) \
            .execute()
        
        logger.info(f"Commission {commission_id} rejected by {current_staff.get('staff_id')}: {reason or 'No reason given'}")
        
        return {
            "success": True,
            "message": "Commission rejected"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting commission {commission_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to reject commission")


@router.get("/commissions/summary")
async def get_commission_summary(current_staff: dict = Depends(verify_admin)):
    """
    Get summary of all commissions by status.
    """
    supabase = get_supabase()
    
    try:
        result = supabase.table("sales_commissions") \
            .select("status, amount") \
            .execute()
        
        commissions = result.data or []
        
        summary = {
            "held": {"count": 0, "total": 0},
            "pending_approval": {"count": 0, "total": 0},
            "released": {"count": 0, "total": 0},
            "rejected": {"count": 0, "total": 0},
            "pending": {"count": 0, "total": 0}
        }
        
        for c in commissions:
            status = c["status"] or "pending"
            if status in summary:
                summary[status]["count"] += 1
                summary[status]["total"] += float(c["amount"] or 0)
        
        return {
            "success": True,
            "summary": summary
        }
    
    except Exception as e:
        logger.error(f"Error fetching commission summary: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch summary")