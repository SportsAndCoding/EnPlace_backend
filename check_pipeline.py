from database.supabase_client import supabase
result = supabase.table('pipeline_run_log').select('*').order('run_date', desc=True).limit(3).execute()
for r in result.data:
    print(f\\\"{r['run_date']}: {r['status']} - checkins:{r.get('checkins_generated',0)}, risks:{r.get('risks_calculated',0)}\\\")