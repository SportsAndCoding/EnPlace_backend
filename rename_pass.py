#!/usr/bin/env python3
"""
BACKEND RENAME PASS
===================
Walks backend_final/ and applies the platform migration code-side changes:

  Mechanical:
    - restaurant_id     -> organization_id   (variables, params, dict keys, strings)
    - .table("restaurants")    -> .table("organizations")
    - .table("restaurant_groups") -> .table("organization_groups")
    - .table("synthetic_restaurants") -> .table("synthetic_organizations")
    - restaurant_type   -> organization_subtype

  Surgical (specific call sites in escalation_monitor_service.py and dashboard_service.py):
    - get_positions_for_display_role(affected_role)
        -> get_positions_for_display_role(self.supabase, organization_id, affected_role)
    - compute_burnout(...) signature gets organization_id parameter
    - compute_burnout(...) call site passes organization_id
    - get_role_category(role) -> get_role_category(supabase, organization_id, role)

Skips anonymity_guard.py since it was already updated by hand.

USAGE
-----
    cd C:\\dev\\restaurant-simulator\\backend_final
    python rename_pass.py --dry-run     # preview without writing
    python rename_pass.py               # apply changes

Or pass --root explicitly:
    python rename_pass.py --root "C:\\dev\\restaurant-simulator\\backend_final"
"""

import re
import argparse
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# REPLACEMENTS
# ─────────────────────────────────────────────────────────────────────────────
# Order matters. More specific patterns run first.

REPLACEMENTS = [
    # ── Surgical: anonymity_guard call site updates ──
    # These run before the bulk renames so they match the original code shape.

    # escalation_monitor_service.py: thread (self.supabase, organization_id) into the call
    (
        r'get_positions_for_display_role\(affected_role\)',
        r'get_positions_for_display_role(self.supabase, organization_id, affected_role)',
    ),
    # dashboard_service.py: thread (supabase, organization_id) into the call
    (
        r'display_role = get_role_category\(role\)',
        r'display_role = get_role_category(supabase, organization_id, role)',
    ),
    # dashboard_service.py: add organization_id parameter to compute_burnout signature
    (
        r'def compute_burnout\(checkins_7d: list, checkins_28d: list, shifts_week: list, staff_list: list\) -> dict:',
        r'def compute_burnout(checkins_7d: list, checkins_28d: list, shifts_week: list, staff_list: list, organization_id: int) -> dict:',
    ),
    # dashboard_service.py: pass organization_id at the call site in get_dashboard_data
    (
        r'burnout = compute_burnout\(checkins_7d, checkins_28d, shifts_week, staff_list\)',
        r'burnout = compute_burnout(checkins_7d, checkins_28d, shifts_week, staff_list, organization_id)',
    ),

    # ── Mechanical: database table names (scoped to .table() calls) ──
    (r'\.table\("restaurants"\)', '.table("organizations")'),
    (r"\.table\('restaurants'\)", ".table('organizations')"),
    (r'\.table\("restaurant_groups"\)', '.table("organization_groups")'),
    (r"\.table\('restaurant_groups'\)", ".table('organization_groups')"),
    (r'\.table\("synthetic_restaurants"\)', '.table("synthetic_organizations")'),
    (r"\.table\('synthetic_restaurants'\)", ".table('synthetic_organizations')"),

    # ── Mechanical: column references in Supabase queries (string literals) ──
    (r'"restaurant_id"', '"organization_id"'),
    (r"'restaurant_id'", "'organization_id'"),
    (r'"restaurant_type"', '"organization_subtype"'),
    (r"'restaurant_type'", "'organization_subtype'"),

    # ── Mechanical: variable / parameter / attribute names ──
    # Word boundaries prevent matches inside other identifiers.
    (r'\brestaurant_id\b', 'organization_id'),
    (r'\brestaurant_type\b', 'organization_subtype'),
]

# Files we should never touch
SKIP_FILES = {
    'anonymity_guard.py',  # already updated by hand
    'rename_pass.py',      # this script itself
}

# Directories to skip during the walk
SKIP_DIRS = {
    '__pycache__', 'venv', '.venv', 'env', 'node_modules', '.git',
    '.pytest_cache', '.mypy_cache', 'build', 'dist', '.idea', '.vscode',
    'migrations',
}


def process_file(path: Path, dry_run: bool):
    """Apply all replacements to a file. Returns (total_changes, change_list)."""
    try:
        content = path.read_text(encoding='utf-8')
    except (UnicodeDecodeError, IOError):
        return 0, None

    original = content
    changes = []

    for pattern, replacement in REPLACEMENTS:
        new_content, count = re.subn(pattern, replacement, content)
        if count > 0:
            changes.append((pattern, replacement, count))
            content = new_content

    if content != original and not dry_run:
        path.write_text(content, encoding='utf-8')

    return sum(c[2] for c in changes), changes


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--root', default='.',
        help='Path to backend_final directory (default: current directory)'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would change without modifying files'
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"Path not found: {root}", file=sys.stderr)
        sys.exit(1)

    mode = "DRY RUN: " if args.dry_run else ""
    print(f"{mode}Scanning {root}")
    print()

    total_files = 0
    changed_files = 0
    total_changes = 0

    for py_file in sorted(root.rglob('*.py')):
        if any(skip in py_file.parts for skip in SKIP_DIRS):
            continue
        if py_file.name in SKIP_FILES:
            continue

        total_files += 1
        count, changes = process_file(py_file, args.dry_run)
        if count > 0:
            changed_files += 1
            total_changes += count
            try:
                rel_path = py_file.relative_to(root)
            except ValueError:
                rel_path = py_file
            print(f"{rel_path}: {count} changes")
            for pattern, replacement, c in changes:
                # Truncate long patterns for readable output
                display_pattern = pattern if len(pattern) < 60 else pattern[:57] + '...'
                display_repl = replacement if len(replacement) < 60 else replacement[:57] + '...'
                print(f"    {display_pattern} -> {display_repl} (x{c})")

    print()
    print(f"Files scanned:  {total_files}")
    print(f"Files changed:  {changed_files}")
    print(f"Total changes:  {total_changes}")

    if args.dry_run:
        print()
        print("DRY RUN: no files were modified. Re-run without --dry-run to apply.")


if __name__ == '__main__':
    main()
