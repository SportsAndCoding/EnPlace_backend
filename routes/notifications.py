from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Optional
from services.auth_service import verify_jwt_token as get_current_user
from services.notifications_service import NotificationsService
from models.notifications import (
    NotificationCreate,
    NotificationUpdate,
    NotificationCreateResponse
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

@router.post("", response_model=NotificationCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_notification(
    notification: NotificationCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new notification.
    Managers only.
    
    Set recipient_id to null for broadcast to all managers.
    """
    # Verify manager access
    if current_user['portal_access'] != 'manager':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can create notifications"
        )
    
    # Verify restaurant access
    if current_user['restaurant_id'] != notification.restaurant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    service = NotificationsService()
    
    try:
        result = await service.create_notification(notification.dict())
        
        return NotificationCreateResponse(
            success=True,
            notification_id=str(result['id']),
            message="Notification created"
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create notification: {str(e)}"
        )


@router.get("")
async def get_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, le=100),
    current_user: dict = Depends(get_current_user)
):
    """
    Get notifications for current user.
    
    Includes:
    - Direct notifications (sent to this user)
    - Broadcast notifications (sent to all at this restaurant)
    
    Optional filters:
    - unread_only: Only return unread notifications
    - limit: Max results (default 50, max 100)
    """
    service = NotificationsService()
    
    try:
        notifications = await service.get_notifications_for_user(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id'],
            unread_only=unread_only,
            limit=limit
        )
        
        unread_count = await service.get_unread_count(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id']
        )
        
        return {
            "success": True,
            "notifications": notifications,
            "count": len(notifications),
            "unread_count": unread_count
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch notifications: {str(e)}"
        )


@router.put("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Mark a notification as read"""
    service = NotificationsService()
    
    try:
        result = await service.mark_as_read(
            notification_id=notification_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found"
            )
        
        return {
            "success": True,
            "notification": result,
            "message": "Marked as read"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update notification: {str(e)}"
        )


@router.put("/read-all")
async def mark_all_notifications_read(
    current_user: dict = Depends(get_current_user)
):
    """Mark all notifications as read for current user"""
    service = NotificationsService()
    
    try:
        count = await service.mark_all_as_read(
            staff_id=current_user['staff_id'],
            restaurant_id=current_user['restaurant_id']
        )
        
        return {
            "success": True,
            "marked_count": count,
            "message": f"Marked {count} notifications as read"
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update notifications: {str(e)}"
        )


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a notification"""
    service = NotificationsService()
    
    try:
        # Verify notification exists
        existing = await service.get_notification_by_id(
            notification_id=notification_id,
            restaurant_id=current_user['restaurant_id']
        )
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found"
            )
        
        await service.delete_notification(
            notification_id=notification_id,
            restaurant_id=current_user['restaurant_id']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete notification: {str(e)}"
        )