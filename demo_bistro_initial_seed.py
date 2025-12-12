import os
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client
from modules.nightly_pipeline.demo_bistro_seeder import seed_demo_bistro_history

client = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_KEY'))
count = seed_demo_bistro_history(client, days_back=30, restaurant_id=1)
print(f'Seeded {count} check-ins')