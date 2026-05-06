from database.supabase_client import supabase
r = supabase.table("staff_graph_metrics").select("staff_id, priority_tier, role_label, cascade_risk, connected_staff_count, retention_score").eq("organization_id", 1).order("retention_score", desc=True).limit(15).execute()
for s in r.data:
    print(f"{s['staff_id']:25s} tier={s['priority_tier']:10s} role={s['role_label']:12s} connections={s['connected_staff_count']}  cascade={s['cascade_risk']}")