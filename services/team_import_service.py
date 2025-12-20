# services/team_import_service.py
"""
Team Import Service (Onboarding Magic Import)
=============================================
Extracts staff names and positions from pasted schedules or CSV data.
Used during onboarding to quickly populate a restaurant's team.

NO DATABASE ACCESS - pure text parsing.

Pipeline:
1. Normalize input (strip dates, times, noise)
2. Extract candidate name-role pairs
3. Match roles via hardcoded keywords (fuzzy)
4. OpenAI fallback for unmatched
5. Split names into first/last
6. Deduplicate and flag
"""

import re
import os
import csv
import logging
from io import StringIO
from typing import List, Dict, Optional, Tuple
from difflib import SequenceMatcher
from collections import Counter

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Initialize OpenAI client
openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ═══════════════════════════════════════════════════════════════════════════════
# HARDCODED POSITION KEYWORDS
# ═══════════════════════════════════════════════════════════════════════════════

POSITION_KEYWORDS = {
    # FOH
    "server": "Server",
    "waiter": "Server",
    "waitress": "Server",
    "svr": "Server",
    "srv": "Server",
    "host": "Host",
    "hostess": "Host",
    "greeter": "Host",
    "busser": "Busser",
    "busboy": "Busser",
    "bus": "Busser",
    "bssr": "Busser",
    "runner": "Food Runner",
    "food runner": "Food Runner",
    "fr": "Food Runner",
    "expo": "Expo",
    "expeditor": "Expo",
    "expediter": "Expo",
    "bartender": "Bartender",
    "bartndr": "Bartender",
    "bar": "Bartender",
    "bt": "Bartender",
    "barback": "Barback",
    "bar back": "Barback",
    "bb": "Barback",
    "backwaiter": "Backwaiter",
    "back waiter": "Backwaiter",
    "captain": "Captain",
    "cashier": "Cashier",
    
    # BOH
    "cook": "Cook",
    "line cook": "Line Cook",
    "line": "Line Cook",
    "lc": "Line Cook",
    "prep cook": "Prep Cook",
    "prep": "Prep Cook",
    "saute": "Line Cook",
    "sauté": "Line Cook",
    "grill": "Line Cook",
    "fry": "Line Cook",
    "fryer": "Line Cook",
    "dishwasher": "Dishwasher",
    "dish": "Dishwasher",
    "dw": "Dishwasher",
    "steward": "Dishwasher",
    "porter": "Porter",
    "kitchen": "BOH Team Member",
    "boh": "BOH Team Member",
    
    # Management
    "manager": "Manager",
    "mgr": "Manager",
    "mod": "Manager",
    "gm": "General Manager",
    "general manager": "General Manager",
    "agm": "Assistant Manager",
    "assistant manager": "Assistant Manager",
    "asst manager": "Assistant Manager",
    "shift lead": "Shift Lead",
    "shift leader": "Shift Lead",
    "sl": "Shift Lead",
    "supervisor": "Supervisor",
    "sup": "Supervisor",
    "owner": "Owner",
    "chef": "Chef",
    "executive chef": "Executive Chef",
    "exec chef": "Executive Chef",
    "head chef": "Executive Chef",
    "sous chef": "Sous Chef",
    "sous": "Sous Chef",
    "km": "Kitchen Manager",
    "kitchen manager": "Kitchen Manager",
    "foh manager": "FOH Manager",
    "foh mgr": "FOH Manager",
    "boh manager": "BOH Manager",
    "boh mgr": "BOH Manager",
    
    # Generic
    "team member": "Team Member",
    "staff": "Team Member",
    "employee": "Team Member",
    "foh": "FOH Team Member",
}

# Section headers that indicate context
BOH_SECTION_KEYWORDS = ["kitchen", "boh", "back of house", "cooks", "dish"]
FOH_SECTION_KEYWORDS = ["floor", "foh", "front of house", "servers", "bar", "dining"]


# ═══════════════════════════════════════════════════════════════════════════════
# NAME SPLITTING
# ═══════════════════════════════════════════════════════════════════════════════

def split_name(full_name: str) -> Tuple[str, str]:
    """
    Split a full name into first and last name.
    Handles various formats:
    - "John Smith" -> ("John", "Smith")
    - "John" -> ("John", "")
    - "John Michael Smith" -> ("John", "Michael Smith")
    - "J. Smith" -> ("J.", "Smith")
    """
    full_name = full_name.strip()
    parts = full_name.split()
    
    if len(parts) == 0:
        return ("", "")
    elif len(parts) == 1:
        return (parts[0], "")
    else:
        return (parts[0], " ".join(parts[1:]))


# ═══════════════════════════════════════════════════════════════════════════════
# FUZZY MATCHING
# ═══════════════════════════════════════════════════════════════════════════════

def fuzzy_match_position(text: str, threshold: float = 0.7) -> Optional[str]:
    """
    Fuzzy match text against position keywords.
    Returns normalized position name or None.
    """
    text_lower = text.lower().strip()
    
    # Exact match first
    if text_lower in POSITION_KEYWORDS:
        return POSITION_KEYWORDS[text_lower]
    
    # Check if keyword is contained in text
    for keyword, position in POSITION_KEYWORDS.items():
        if keyword in text_lower:
            return position
    
    # Fuzzy match
    best_match = None
    best_score = 0
    
    for keyword, position in POSITION_KEYWORDS.items():
        score = SequenceMatcher(None, text_lower, keyword).ratio()
        if score > best_score and score >= threshold:
            best_score = score
            best_match = position
    
    return best_match


# ═══════════════════════════════════════════════════════════════════════════════
# TEXT PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def is_likely_name(text: str) -> bool:
    """Check if text looks like a person's name."""
    text = text.strip()
    # Remove quotes that might wrap CSV values
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    
    if len(text) < 2 or len(text) > 50:
        return False
    if re.search(r'\d', text):
        return False
    if text.lower() in POSITION_KEYWORDS:
        return False
    
    skip_words = {'off', 'on', 'call', 'open', 'close', 'shift', 'schedule', 
                  'week', 'day', 'hours', 'total', 'notes', 'n/a', 'tbd', 'na',
                  'morning', 'evening', 'night', 'lunch', 'dinner', 'brunch',
                  'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 
                  'saturday', 'sunday', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'}
    if text.lower() in skip_words:
        return False
    
    # First char should be a letter (including unicode letters)
    if not text[0].isalpha():
        return False
    
    # Should contain mostly letters, spaces, and name punctuation (apostrophes, hyphens, periods)
    # Allow unicode letters for names like Rodríguez
    clean = re.sub(r"['\-.\s''`]", '', text)
    if not all(c.isalpha() for c in clean):
        return False
    
    return True


def normalize_name(name: str) -> str:
    """Normalize a name to Title Case and clean up."""
    # Remove surrounding quotes
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    
    # Normalize whitespace
    name = ' '.join(name.split())
    
    # Normalize apostrophes and quotes
    name = name.replace(''', "'").replace(''', "'").replace('`', "'")
    
    parts = name.split()
    normalized_parts = []
    
    for part in parts:
        if len(part) <= 2 and part.endswith('.'):
            normalized_parts.append(part.upper())
        else:
            normalized_parts.append(part.title())
    
    return ' '.join(normalized_parts)


def infer_position_from_context(section_context: Optional[str]) -> str:
    """Infer position from section context or return generic."""
    if section_context == "BOH":
        return "BOH Team Member"
    elif section_context == "FOH":
        return "FOH Team Member"
    return "Team Member"


def parse_line_for_staff(line: str, section_context: Optional[str] = None) -> Optional[Dict]:
    """Parse a single line to extract name and position."""
    
    # Pattern 1: Name - Position or Name, Position
    match = re.match(r'^([A-Za-z][A-Za-z\s.\']+?)\s*[-,|:]\s*(.+)$', line)
    if match:
        name_part = match.group(1).strip()
        role_part = match.group(2).strip()
        
        if fuzzy_match_position(name_part) and is_likely_name(role_part):
            name_part, role_part = role_part, name_part
        
        if is_likely_name(name_part):
            position = fuzzy_match_position(role_part)
            return {
                'name': normalize_name(name_part),
                'position': position or infer_position_from_context(section_context),
                'confidence': 'high' if position else 'medium',
                'raw_position': role_part if not position else None
            }
    
    # Pattern 2: Name (role) or Name [role]
    match = re.match(r'^([A-Za-z][A-Za-z\s.\']+?)\s*[\(\[](.+?)[\)\]]', line)
    if match:
        name_part = match.group(1).strip()
        role_part = match.group(2).strip()
        
        if is_likely_name(name_part):
            position = fuzzy_match_position(role_part)
            return {
                'name': normalize_name(name_part),
                'position': position or infer_position_from_context(section_context),
                'confidence': 'high' if position else 'medium',
                'raw_position': role_part if not position else None
            }
    
    # Pattern 3: Tab/space separated
    parts = re.split(r'\t+|\s{2,}', line)
    if len(parts) >= 2:
        name_candidate = parts[0].strip()
        if is_likely_name(name_candidate):
            for part in parts[1:]:
                position = fuzzy_match_position(part.strip())
                if position:
                    return {
                        'name': normalize_name(name_candidate),
                        'position': position,
                        'confidence': 'high',
                        'raw_position': None
                    }
            
            return {
                'name': normalize_name(name_candidate),
                'position': infer_position_from_context(section_context),
                'confidence': 'low',
                'raw_position': None
            }
    
    # Pattern 4: Just a name
    if is_likely_name(line) and len(line.split()) <= 4:
        return {
            'name': normalize_name(line),
            'position': infer_position_from_context(section_context),
            'confidence': 'low',
            'raw_position': None
        }
    
    return None


def extract_from_text(raw_text: str) -> List[Dict]:
    """Extract name-position pairs from raw text."""
    
    # Check if this looks like CSV content (has commas and consistent structure)
    lines = raw_text.strip().split('\n')
    if len(lines) > 1:
        first_line_commas = lines[0].count(',')
        if first_line_commas >= 2:
            # Likely CSV - check if most lines have similar comma count
            csv_like = sum(1 for line in lines[1:5] if abs(line.count(',') - first_line_commas) <= 1)
            if csv_like >= min(2, len(lines) - 1):
                # Parse as CSV
                return extract_from_csv(raw_text)
    
    results = []
    current_section_context = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Check for section headers
        line_lower = line.lower()
        if any(kw in line_lower for kw in BOH_SECTION_KEYWORDS):
            current_section_context = "BOH"
            continue
        if any(kw in line_lower for kw in FOH_SECTION_KEYWORDS):
            current_section_context = "FOH"
            continue
        
        pair = parse_line_for_staff(line, current_section_context)
        if pair:
            results.append(pair)
    
    return results


def extract_from_csv(csv_text: str) -> List[Dict]:
    """Extract staff from CSV content."""
    results = []
    
    try:
        reader = csv.DictReader(StringIO(csv_text))
        headers = {h.lower(): h for h in (reader.fieldnames or [])}
        
        # Find columns
        name_col = None
        position_col = None
        first_name_col = None
        last_name_col = None
        
        # Comprehensive list of possible name column headers
        name_candidates = [
            # Canonical
            "name", "full name", "fullname", "full_name",
            
            # Employee variants
            "employee", "employee name", "employee_name", "employee full name", "employee fullname",
            "employeename", "emp name", "emp_name", "emp",
            "employeeid", "employee id", "employee_id", "employee_number", "employee_no",
            "emp_no", "empno",
            
            # Staff/team variants
            "staff", "staff name", "staff_name", "staffname", "staff member", "staffmember", "staff_member",
            "team", "team member", "team_member", "teammember",
            "crew", "crew member", "crew_member", "crewmember", "crewname", "crew_name", "crew name",
            "associate", "associate name", "associate_name", "associatename",
            "worker", "worker name", "worker_name", "workername",
            
            # Person/user/contact variants
            "person", "person name", "person_name", "personname",
            "contact", "contact name", "contact_name", "contactname",
            "member", "member name", "member_name", "membername",
            "user", "user name", "username", "user_name",
            "display name", "display_name", "displayname",
            "preferred name", "preferred_name",
            "legal name", "legal_name",
            "nickname",
            
            # Manager/supervisor (common in org charts)
            "manager", "manager name", "manager_name", "managername",
            "supervisor", "supervisor name", "supervisor_name", "supervisorname",
            
            # Common shorthand/mislabels
            "employee full", "staff full",
            
            # Generic but sometimes used
            "id",
        ]
        for candidate in name_candidates:
            if candidate in headers:
                name_col = headers[candidate]
                break
        
        position_candidates = ['position', 'role', 'title', 'job', 'job title', 'job_title', 
                               'jobtitle', 'department', 'dept', 'classification']
        for candidate in position_candidates:
            if candidate in headers:
                position_col = headers[candidate]
                break
        
        # First name column detection
        first_name_candidates = ['first name', 'firstname', 'first_name', 'given name', 
                                 'given_name', 'givenname', 'fname', 'f_name', 'first']
        for candidate in first_name_candidates:
            if candidate in headers:
                first_name_col = headers[candidate]
                break
        
        # Last name column detection  
        last_name_candidates = ['last name', 'lastname', 'last_name', 'surname', 
                                'family name', 'family_name', 'familyname', 'lname', 
                                'l_name', 'last']
        for candidate in last_name_candidates:
            if candidate in headers:
                last_name_col = headers[candidate]
                break
        
        for row in reader:
            # Build name
            if name_col and row.get(name_col):
                name = row[name_col].strip()
            elif first_name_col and last_name_col:
                first = row.get(first_name_col, '').strip()
                last = row.get(last_name_col, '').strip()
                name = f"{first} {last}".strip()
            else:
                continue
            
            if not is_likely_name(name):
                continue
            
            raw_position = row.get(position_col, '').strip() if position_col else ''
            position = fuzzy_match_position(raw_position) if raw_position else None
            
            results.append({
                'name': normalize_name(name),
                'position': position or 'Team Member',
                'confidence': 'high' if position else 'medium',
                'raw_position': raw_position if not position and raw_position else None
            })
    
    except Exception as e:
        logger.error(f"CSV parsing error: {e}")
    
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# OPENAI FALLBACK
# ═══════════════════════════════════════════════════════════════════════════════

async def openai_extract_roles(unmatched_roles: List[str]) -> Dict[str, str]:
    """Use OpenAI to extract roles from unmatched text."""
    if not unmatched_roles:
        return {}
    
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        logger.warning("No OPENAI_API_KEY set, skipping LLM fallback")
        return {}
    
    try:
        roles_text = '\n'.join([f"- {r}" for r in unmatched_roles[:20]])
        
        prompt = f"""You are extracting restaurant staff positions from schedule text.

For each line below, output ONLY the standardized position name. Use these exact names:
Server, Host, Busser, Food Runner, Expo, Bartender, Barback, Cook, Line Cook, Prep Cook, Dishwasher, Manager, General Manager, Assistant Manager, Shift Lead, Chef, Sous Chef, Team Member

If you cannot determine the position, output "Team Member".

Lines to analyze:
{roles_text}

Output format (one per line, same order as input):
position1
position2
..."""

        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=500
        )
        
        response_text = response.choices[0].message.content.strip()
        response_lines = response_text.split('\n')
        
        result = {}
        for i, raw_role in enumerate(unmatched_roles[:20]):
            if i < len(response_lines):
                result[raw_role] = response_lines[i].strip()
            else:
                result[raw_role] = "Team Member"
        
        return result
    
    except Exception as e:
        logger.error(f"OpenAI extraction error: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# DUPLICATE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# DEDUPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_name_for_dedup(name: str) -> str:
    """Normalize name for deduplication matching."""
    # Remove extra whitespace, lowercase, strip punctuation variations
    name = ' '.join(name.lower().split())
    # Normalize apostrophes
    name = name.replace(''', "'").replace(''', "'")
    # Remove suffixes for matching
    for suffix in [' jr.', ' jr', ' sr.', ' sr', ' ii', ' iii', ' iv']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name.strip()


def deduplicate_staff(staff_list: List[Dict]) -> List[Dict]:
    """
    Deduplicate staff entries from schedules.
    
    Schedules have the same person on multiple days/shifts.
    We want unique people, keeping the best position match.
    """
    # Group by normalized name
    name_groups: Dict[str, List[Dict]] = {}
    
    for staff in staff_list:
        key = normalize_name_for_dedup(staff['name'])
        if not key:
            continue
        if key not in name_groups:
            name_groups[key] = []
        name_groups[key].append(staff)
    
    # For each group, pick the best entry
    confidence_rank = {'high': 3, 'medium': 2, 'low': 1}
    deduplicated = []
    
    for key, entries in name_groups.items():
        if len(entries) == 1:
            deduplicated.append(entries[0])
        else:
            # Sort by confidence (highest first), then by position specificity
            entries.sort(key=lambda x: (
                confidence_rank.get(x.get('confidence', 'low'), 0),
                0 if x.get('position') == 'Team Member' else 1  # Prefer specific positions
            ), reverse=True)
            
            best = entries[0]
            
            # Check if there are conflicting positions (different high-confidence positions)
            positions = set(e['position'] for e in entries if e.get('confidence') == 'high')
            if len(positions) > 1:
                # Multiple valid positions - might be different people or multi-role
                # Keep the most common one
                position_counts = Counter(e['position'] for e in entries)
                best['position'] = position_counts.most_common(1)[0][0]
            
            deduplicated.append(best)
    
    return deduplicated


def detect_duplicates(staff_list: List[Dict]) -> List[Dict]:
    """Flag potential duplicates based on name matching."""
    name_counts = Counter(s['name'].lower() for s in staff_list)
    
    for staff in staff_list:
        name_lower = staff['name'].lower()
        staff['duplicate_warning'] = name_counts[name_lower] > 1
    
    return staff_list


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

async def extract_team_from_text(
    raw_text: Optional[str] = None,
    csv_content: Optional[str] = None,
    use_llm_fallback: bool = True
) -> Dict:
    """
    Main entry point for team extraction.
    
    Returns:
        {
            success: bool,
            staff: [{name, first_name, last_name, position, confidence, duplicate_warning}],
            stats: {total, high_confidence, medium_confidence, low_confidence}
        }
    """
    staff_list = []
    
    # Parse based on input type
    if csv_content:
        staff_list = extract_from_csv(csv_content)
    elif raw_text:
        staff_list = extract_from_text(raw_text)
    else:
        return {
            'success': False,
            'error': 'No input provided',
            'staff': [],
            'stats': {}
        }
    
    if not staff_list:
        return {
            'success': False,
            'error': 'No staff members found. Try a different format or add manually.',
            'staff': [],
            'stats': {}
        }
    
    # Deduplicate - schedules have same person on multiple days
    staff_list = deduplicate_staff(staff_list)
    
    # LLM fallback for unmatched roles
    if use_llm_fallback:
        unmatched = [s['raw_position'] for s in staff_list 
                    if s.get('raw_position') and s['confidence'] != 'high']
        
        if unmatched:
            llm_results = await openai_extract_roles(list(set(unmatched)))
            
            for staff in staff_list:
                if staff.get('raw_position') in llm_results:
                    staff['position'] = llm_results[staff['raw_position']]
                    staff['confidence'] = 'medium'
    
    # Clean up and split names
    for staff in staff_list:
        staff.pop('raw_position', None)
        first, last = split_name(staff['name'])
        staff['first_name'] = first
        staff['last_name'] = last
    
    # Detect duplicates
    staff_list = detect_duplicates(staff_list)
    
    # Stats
    stats = {
        'total': len(staff_list),
        'high_confidence': sum(1 for s in staff_list if s['confidence'] == 'high'),
        'medium_confidence': sum(1 for s in staff_list if s['confidence'] == 'medium'),
        'low_confidence': sum(1 for s in staff_list if s['confidence'] == 'low'),
        'duplicates_flagged': sum(1 for s in staff_list if s.get('duplicate_warning'))
    }
    
    return {
        'success': True,
        'staff': staff_list,
        'stats': stats
    }