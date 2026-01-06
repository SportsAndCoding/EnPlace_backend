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