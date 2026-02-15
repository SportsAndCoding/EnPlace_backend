from modules.nightly_pipeline.graph_pipeline import process_restaurant_graph
from datetime import date, timedelta

yesterday = date.today() - timedelta(days=1)
print(f"Running graph pipeline for Demo Bistro, date: {yesterday}")
result = process_restaurant_graph(restaurant_id=1, target_date=yesterday)
print(result)