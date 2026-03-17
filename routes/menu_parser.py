"""
MENU PARSER ROUTES
AI-powered menu extraction. Async: submit returns immediately,
background task processes, frontend polls for result.
"""
import os
import re
import json
import logging
import threading
import httpx
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from services.auth_service import verify_jwt_token
from database.supabase_client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/menu-parser", tags=["menu-parser"])

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


class MenuParseRequest(BaseModel):
    url: Optional[str] = None
    raw_text: Optional[str] = None

from fastapi import File, UploadFile


@router.post("/parse-pdf")
async def parse_menu_pdf(file: UploadFile = File(...), user=Depends(verify_jwt_token)):
    """Parse a menu from an uploaded PDF file."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Anthropic API key not configured")

    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    try:
        import pdfplumber
        import io

        content = await file.read()
        pdf = pdfplumber.open(io.BytesIO(content))
        text = ""
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"
        pdf.close()

        if not text.strip():
            raise HTTPException(status_code=400, detail="Could not extract text from PDF")

        # Reuse the async flow
        task_id = str(uuid4())
        supabase = get_supabase()
        supabase.table("menu_parse_jobs").insert({
            "id": task_id,
            "status": "processing",
            "input_url": file.filename,
            "input_text": text[:500],
        }).execute()

        thread = threading.Thread(
            target=run_menu_parse,
            args=(task_id, None, text[:12000]),
            daemon=True
        )
        thread.start()

        return {"success": True, "task_id": task_id, "status": "processing"}

    except Exception as e:
        logger.error(f"PDF parse error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ═══════════════════════════════════════════════════════════════════════════════
# SUBMIT (returns immediately)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/parse")
async def parse_menu(req: MenuParseRequest, user=Depends(verify_jwt_token)):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Anthropic API key not configured")
    if not req.url and not req.raw_text:
        raise HTTPException(status_code=400, detail="Provide a menu URL or raw text")

    task_id = str(uuid4())

    # Create the job record
    supabase = get_supabase()
    supabase.table("menu_parse_jobs").insert({
        "id": task_id,
        "status": "processing",
        "input_url": req.url,
        "input_text": (req.raw_text or "")[:500],  # Store just a preview
    }).execute()

    # Run the actual parsing in a background thread
    thread = threading.Thread(
        target=run_menu_parse,
        args=(task_id, req.url, req.raw_text),
        daemon=True
    )
    thread.start()

    return {"success": True, "task_id": task_id, "status": "processing"}


# ═══════════════════════════════════════════════════════════════════════════════
# POLL FOR RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/result/{task_id}")
async def get_parse_result(task_id: str, user=Depends(verify_jwt_token)):
    supabase = get_supabase()
    result = supabase.table("menu_parse_jobs") \
        .select("status, result, error_message") \
        .eq("id", task_id) \
        .single() \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")

    job = result.data
    if job["status"] == "processing":
        return {"status": "processing"}
    elif job["status"] == "completed":
        return {
            "status": "completed",
            "success": True,
            "menu_highlights": job["result"]["menu_highlights"],
            "categories": job["result"]["categories"],
            "total_items": job["result"]["total_items"]
        }
    else:
        return {"status": "failed", "error": job.get("error_message", "Unknown error")}


# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND WORKER (runs in thread, no timeout)
# ═══════════════════════════════════════════════════════════════════════════════

def run_menu_parse(task_id, url, raw_text):
    """Runs in a background thread. Fetches menu, calls Claude, saves result."""
    from supabase import create_client
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    try:
        menu_text = raw_text or ""

        # Fetch URL if provided
        if url and not menu_text:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                resp = client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                resp.raise_for_status()
                html = resp.text

                # Strip non-content HTML
                text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL)
                text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL)
                text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                menu_text = text[:12000]

        if not menu_text.strip():
            raise ValueError("No menu content found at that URL")

        # Call Claude
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        prompt = f"""You are a restaurant menu parser. Extract EVERY menu item from the following menu text and organize it into categories.

MENU TEXT:
{menu_text}

Return ONLY a valid JSON array. No markdown, no backticks, no explanation.

Structure:
[
  {{
    "icon": "emoji that fits this category",
    "category": "Category Name",
    "items": [
      {{
        "name": "Item Name",
        "note": "Brief description if available, otherwise empty string",
        "price": "$XX.XX or Market Price or empty string if not listed"
      }}
    ]
  }}
]

RULES:
- Extract EVERY item. Do not summarize or skip items.
- Group items into logical categories (Appetizers, Sushi Rolls, Hibachi Entrees, etc.)
- Use the restaurant's own category names when they exist in the text.
- If an item has a description, put it in "note". Keep it under 60 characters.
- If an item has multiple sizes/prices, use the most common price or the dinner price.
- Use appropriate emoji for each category.
- Prices should include the $ sign.
- Return ONLY the JSON array."""

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = ""
        for block in message.content:
            if block.type == "text":
                response_text += block.text

        response_text = response_text.strip()
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:])
        if response_text.endswith("```"):
            response_text = response_text[:-3].strip()
        if response_text.startswith("json"):
            response_text = response_text[4:].strip()

        try:
            menu = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("[")
            end = response_text.rfind("]") + 1
            if start >= 0 and end > start:
                menu = json.loads(response_text[start:end])
            else:
                raise ValueError("Could not parse menu JSON from AI response")

        total_items = sum(len(cat.get("items", [])) for cat in menu)

        # Save result
        sb.table("menu_parse_jobs").update({
            "status": "completed",
            "result": {
                "menu_highlights": menu,
                "categories": len(menu),
                "total_items": total_items
            }
        }).eq("id", task_id).execute()

        logger.info(f"Menu parse {task_id} completed: {total_items} items in {len(menu)} categories")

    except Exception as e:
        logger.error(f"Menu parse {task_id} failed: {e}")
        sb.table("menu_parse_jobs").update({
            "status": "failed",
            "error_message": str(e)[:500]
        }).eq("id", task_id).execute()