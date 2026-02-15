from modules.nightly_pipeline.graph_pipeline import process_restaurant_graph
from datetime import date

today = date.today()
print(f"Running graph pipeline for Demo Bistro, date: {today}")
result = process_restaurant_graph(restaurant_id=1, target_date=today)
print(result)