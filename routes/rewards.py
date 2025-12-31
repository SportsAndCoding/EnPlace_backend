# ═══════════════════════════════════════════════════════════════════════════════
# REWARDS API ROUTER
# Handles earning rules, reward catalog, and redemptions
# ═══════════════════════════════════════════════════════════════════════════════

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import logging

# Import your existing auth and database utilities
# Adjust these imports based on your actual file structure
from services.auth_service import verify_jwt_token
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rewards", tags=["rewards"])


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class EarningRuleUpdate(BaseModel):
    points: int
    is_enabled: bool

class EarningRulesUpdateRequest(BaseModel):
    rules: dict[str, EarningRuleUpdate]  # rule_key -> {points, is_enabled}

class CatalogItemUpdate(BaseModel):
    cost: int
    requires_approval: bool
    is_enabled: bool

class CatalogUpdateRequest(BaseModel):
    items: dict[str, CatalogItemUpdate]  # item_key -> {cost, requires_approval, is_enabled}

class NewCatalogItem(BaseModel):
    name: str
    description: Optional[str] = ""
    icon: str = "🎁"
    category: str = "perks"
    cost: int
    requires_approval: bool = False

class RedemptionRequest(BaseModel):
    catalog_item_id: str

class RedemptionResolution(BaseModel):
    status: str  # 'approved', 'fulfilled', 'declined'
    notes: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# EARNING RULES ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/earning-rules")
async def get_earning_rules(user: dict = Depends(get_current_user)):
    """Get all earning rules for the user's restaurant"""
    try:
        supabase = get_supabase()
        restaurant_id = user.get("restaurant_id")
        
        result = supabase.table("reward_earning_rules") \
            .select("*") \
            .eq("restaurant_id", restaurant_id) \
            .order("rule_key") \
            .execute()
        
        return {"success": True, "rules": result.data}
    
    except Exception as e:
        logger.error(f"Error fetching earning rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/earning-rules")
async def update_earning_rules(
    request: EarningRulesUpdateRequest,
    user: dict = Depends(require_manager)
):
    """Update earning rules (manager only)"""
    try:
        supabase = get_supabase()
        restaurant_id = user.get("restaurant_id")
        
        for rule_key, updates in request.rules.items():
            supabase.table("reward_earning_rules") \
                .update({
                    "points": updates.points,
                    "is_enabled": updates.is_enabled,
                    "updated_at": datetime.utcnow().isoformat()
                }) \
                .eq("restaurant_id", restaurant_id) \
                .eq("rule_key", rule_key) \
                .execute()
        
        return {"success": True, "message": "Earning rules updated"}
    
    except Exception as e:
        logger.error(f"Error updating earning rules: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# REWARD CATALOG ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/catalog")
async def get_reward_catalog(user: dict = Depends(get_current_user)):
    """Get reward catalog for the user's restaurant"""
    try:
        supabase = get_supabase()
        restaurant_id = user.get("restaurant_id")
        
        result = supabase.table("reward_catalog") \
            .select("*") \
            .eq("restaurant_id", restaurant_id) \
            .order("sort_order") \
            .execute()
        
        return {"success": True, "catalog": result.data}
    
    except Exception as e:
        logger.error(f"Error fetching reward catalog: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/catalog/available")
async def get_available_rewards(user: dict = Depends(get_current_user)):
    """Get only enabled rewards for staff portal"""
    try:
        supabase = get_supabase()
        restaurant_id = user.get("restaurant_id")
        
        result = supabase.table("reward_catalog") \
            .select("id, item_key, name, description, icon, category, cost, requires_approval") \
            .eq("restaurant_id", restaurant_id) \
            .eq("is_enabled", True) \
            .order("sort_order") \
            .execute()
        
        return {"success": True, "catalog": result.data}
    
    except Exception as e:
        logger.error(f"Error fetching available rewards: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/catalog")
async def update_reward_catalog(
    request: CatalogUpdateRequest,
    user: dict = Depends(require_manager)
):
    """Update reward catalog items (manager only)"""
    try:
        supabase = get_supabase()
        restaurant_id = user.get("restaurant_id")
        
        for item_key, updates in request.items.items():
            supabase.table("reward_catalog") \
                .update({
                    "cost": updates.cost,
                    "requires_approval": updates.requires_approval,
                    "is_enabled": updates.is_enabled,
                    "updated_at": datetime.utcnow().isoformat()
                }) \
                .eq("restaurant_id", restaurant_id) \
                .eq("item_key", item_key) \
                .execute()
        
        return {"success": True, "message": "Reward catalog updated"}
    
    except Exception as e:
        logger.error(f"Error updating reward catalog: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/catalog")
async def add_catalog_item(
    item: NewCatalogItem,
    user: dict = Depends(require_manager)
):
    """Add a custom reward item (manager only)"""
    try:
        supabase = get_supabase()
        restaurant_id = user.get("restaurant_id")
        
        # Generate a unique key
        item_key = f"custom_{int(datetime.utcnow().timestamp())}"
        
        # Get max sort_order for this restaurant
        max_order = supabase.table("reward_catalog") \
            .select("sort_order") \
            .eq("restaurant_id", restaurant_id) \
            .order("sort_order", desc=True) \
            .limit(1) \
            .execute()
        
        next_order = (max_order.data[0]["sort_order"] + 1) if max_order.data else 100
        
        result = supabase.table("reward_catalog") \
            .insert({
                "restaurant_id": restaurant_id,
                "item_key": item_key,
                "name": item.name,
                "description": item.description,
                "icon": item.icon,
                "category": item.category,
                "cost": item.cost,
                "requires_approval": item.requires_approval,
                "is_enabled": True,
                "sort_order": next_order
            }) \
            .execute()
        
        return {"success": True, "item": result.data[0] if result.data else None}
    
    except Exception as e:
        logger.error(f"Error adding catalog item: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/catalog/{item_id}")
async def delete_catalog_item(
    item_id: str,
    user: dict = Depends(require_manager)
):
    """Delete a reward item (manager only)"""
    try:
        supabase = get_supabase()
        restaurant_id = user.get("restaurant_id")
        
        # Only allow deleting custom items (those with 'custom_' prefix)
        item = supabase.table("reward_catalog") \
            .select("item_key") \
            .eq("id", item_id) \
            .eq("restaurant_id", restaurant_id) \
            .single() \
            .execute()
        
        if not item.data:
            raise HTTPException(status_code=404, detail="Item not found")
        
        # Delete the item
        supabase.table("reward_catalog") \
            .delete() \
            .eq("id", item_id) \
            .eq("restaurant_id", restaurant_id) \
            .execute()
        
        return {"success": True, "message": "Item deleted"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting catalog item: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# REDEMPTION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/redeem")
async def redeem_reward(
    request: RedemptionRequest,
    user: dict = Depends(get_current_user)
):
    """Staff redeems a reward"""
    try:
        supabase = get_supabase()
        staff_id = user.get("staff_id")
        restaurant_id = user.get("restaurant_id")
        
        # Get the catalog item
        item = supabase.table("reward_catalog") \
            .select("*") \
            .eq("id", request.catalog_item_id) \
            .eq("restaurant_id", restaurant_id) \
            .eq("is_enabled", True) \
            .single() \
            .execute()
        
        if not item.data:
            raise HTTPException(status_code=404, detail="Reward not found or not available")
        
        catalog_item = item.data
        
        # Get staff's current balance
        staff = supabase.table("staff") \
            .select("stability_points_balance, full_name") \
            .eq("staff_id", staff_id) \
            .single() \
            .execute()
        
        if not staff.data:
            raise HTTPException(status_code=404, detail="Staff not found")
        
        current_balance = staff.data.get("stability_points_balance", 0) or 0
        
        if current_balance < catalog_item["cost"]:
            raise HTTPException(status_code=400, detail="Insufficient points")
        
        new_balance = current_balance - catalog_item["cost"]
        
        # Deduct points from staff balance
        supabase.table("staff") \
            .update({"stability_points_balance": new_balance}) \
            .eq("staff_id", staff_id) \
            .execute()
        
        # Log the transaction
        supabase.table("stability_points") \
            .insert({
                "staff_id": staff_id,
                "restaurant_id": restaurant_id,
                "points": -catalog_item["cost"],
                "balance_after": new_balance,
                "transaction_type": "redemption",
                "description": f"Redeemed: {catalog_item['name']}",
                "redemption_item_id": catalog_item["id"],
                "redemption_item_name": catalog_item["name"]
            }) \
            .execute()
        
        # Determine initial status based on approval requirement
        initial_status = "pending" if catalog_item["requires_approval"] else "approved"
        
        # Create redemption record
        redemption = supabase.table("reward_redemptions") \
            .insert({
                "restaurant_id": restaurant_id,
                "staff_id": staff_id,
                "catalog_item_id": catalog_item["id"],
                "item_name": catalog_item["name"],
                "item_icon": catalog_item["icon"],
                "point_cost": catalog_item["cost"],
                "status": initial_status
            }) \
            .execute()
        
        return {
            "success": True,
            "redemption": redemption.data[0] if redemption.data else None,
            "new_balance": new_balance,
            "requires_approval": catalog_item["requires_approval"]
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error redeeming reward: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/redemptions/pending")
async def get_pending_redemptions(user: dict = Depends(require_manager)):
    """Get pending redemptions for Action Board"""
    try:
        supabase = get_supabase()
        restaurant_id = user.get("restaurant_id")
        
        result = supabase.table("reward_redemptions") \
            .select("*, staff:staff_id(full_name, position)") \
            .eq("restaurant_id", restaurant_id) \
            .eq("status", "pending") \
            .order("requested_at", desc=True) \
            .execute()
        
        return {"success": True, "redemptions": result.data}
    
    except Exception as e:
        logger.error(f"Error fetching pending redemptions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/redemptions/history")
async def get_redemption_history(
    limit: int = 50,
    user: dict = Depends(require_manager)
):
    """Get redemption history"""
    try:
        supabase = get_supabase()
        restaurant_id = user.get("restaurant_id")
        
        result = supabase.table("reward_redemptions") \
            .select("*, staff:staff_id(full_name, position)") \
            .eq("restaurant_id", restaurant_id) \
            .order("requested_at", desc=True) \
            .limit(limit) \
            .execute()
        
        return {"success": True, "redemptions": result.data}
    
    except Exception as e:
        logger.error(f"Error fetching redemption history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/redemptions/my")
async def get_my_redemptions(user: dict = Depends(get_current_user)):
    """Get staff member's own redemption history"""
    try:
        supabase = get_supabase()
        staff_id = user.get("staff_id")
        
        result = supabase.table("reward_redemptions") \
            .select("*") \
            .eq("staff_id", staff_id) \
            .order("requested_at", desc=True) \
            .limit(20) \
            .execute()
        
        return {"success": True, "redemptions": result.data}
    
    except Exception as e:
        logger.error(f"Error fetching my redemptions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/redemptions/{redemption_id}")
async def resolve_redemption(
    redemption_id: str,
    resolution: RedemptionResolution,
    user: dict = Depends(require_manager)
):
    """Approve, fulfill, or decline a redemption (manager only)"""
    try:
        supabase = get_supabase()
        restaurant_id = user.get("restaurant_id")
        manager_staff_id = user.get("staff_id")
        
        if resolution.status not in ["approved", "fulfilled", "declined"]:
            raise HTTPException(status_code=400, detail="Invalid status")
        
        # Get the redemption
        redemption = supabase.table("reward_redemptions") \
            .select("*") \
            .eq("id", redemption_id) \
            .eq("restaurant_id", restaurant_id) \
            .single() \
            .execute()
        
        if not redemption.data:
            raise HTTPException(status_code=404, detail="Redemption not found")
        
        # If declining, refund the points
        if resolution.status == "declined":
            staff_id = redemption.data["staff_id"]
            point_cost = redemption.data["point_cost"]
            
            # Get current balance
            staff = supabase.table("staff") \
                .select("stability_points_balance") \
                .eq("staff_id", staff_id) \
                .single() \
                .execute()
            
            current_balance = staff.data.get("stability_points_balance", 0) or 0
            new_balance = current_balance + point_cost
            
            # Refund points
            supabase.table("staff") \
                .update({"stability_points_balance": new_balance}) \
                .eq("staff_id", staff_id) \
                .execute()
            
            # Log the refund transaction
            supabase.table("stability_points") \
                .insert({
                    "staff_id": staff_id,
                    "restaurant_id": restaurant_id,
                    "points": point_cost,
                    "balance_after": new_balance,
                    "transaction_type": "refund",
                    "description": f"Refund: {redemption.data['item_name']} (declined)"
                }) \
                .execute()
        
        # Update the redemption status
        supabase.table("reward_redemptions") \
            .update({
                "status": resolution.status,
                "resolved_at": datetime.utcnow().isoformat(),
                "resolved_by": manager_staff_id,
                "resolution_notes": resolution.notes
            }) \
            .eq("id", redemption_id) \
            .execute()
        
        return {"success": True, "message": f"Redemption {resolution.status}"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resolving redemption: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# STAFF POINTS BALANCE ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/balance")
async def get_my_balance(user: dict = Depends(get_current_user)):
    """Get current staff member's points balance and tier"""
    try:
        supabase = get_supabase()
        staff_id = user.get("staff_id")
        
        staff = supabase.table("staff") \
            .select("stability_points_balance") \
            .eq("staff_id", staff_id) \
            .single() \
            .execute()
        
        balance = staff.data.get("stability_points_balance", 0) or 0
        
        # Calculate tier
        if balance >= 1000:
            tier = {"name": "Diamond", "icon": "💎", "color": "#b9f2ff"}
        elif balance >= 500:
            tier = {"name": "Gold", "icon": "🥇", "color": "#FFD700"}
        elif balance >= 200:
            tier = {"name": "Silver", "icon": "🥈", "color": "#C0C0C0"}
        else:
            tier = {"name": "Bronze", "icon": "🥉", "color": "#CD7F32"}
        
        # Calculate progress to next tier
        if balance < 200:
            next_tier = {"name": "Silver", "icon": "🥈", "threshold": 200}
            progress = (balance / 200) * 100
        elif balance < 500:
            next_tier = {"name": "Gold", "icon": "🥇", "threshold": 500}
            progress = ((balance - 200) / 300) * 100
        elif balance < 1000:
            next_tier = {"name": "Diamond", "icon": "💎", "threshold": 1000}
            progress = ((balance - 500) / 500) * 100
        else:
            next_tier = None
            progress = 100
        
        return {
            "success": True,
            "balance": balance,
            "tier": tier,
            "next_tier": next_tier,
            "progress": round(progress, 1)
        }
    
    except Exception as e:
        logger.error(f"Error fetching balance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_my_points_history(
    limit: int = 20,
    user: dict = Depends(get_current_user)
):
    """Get staff member's points transaction history"""
    try:
        supabase = get_supabase()
        staff_id = user.get("staff_id")
        
        result = supabase.table("stability_points") \
            .select("*") \
            .eq("staff_id", staff_id) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        
        return {"success": True, "history": result.data}
    
    except Exception as e:
        logger.error(f"Error fetching points history: {e}")
        raise HTTPException(status_code=500, detail=str(e))