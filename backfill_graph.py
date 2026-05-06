# backfill_graph.py
from modules.nightly_pipeline.graph_pipeline import process_restaurant_graph
from datetime import date, timedelta

start = date(2025, 12, 18)
end = date(2026, 2, 16)  # today

current = start
total_edges = 0
total_scored = 0

while current <= end:
    result = process_restaurant_graph(organization_id=1, target_date=current)
    edges = result.get("edges_updated", 0)
    scored = result.get("staff_scored", 0)
    cascades = result.get("cascades_computed", 0)
    total_edges = max(total_edges, edges)
    print(f"{current}: edges={edges} scored={scored} cascades={cascades}")
    current += timedelta(days=1)

print(f"\nBackfill complete. Peak edges: {total_edges}")