import json
from services.supabase_client import supabase
result = supabase.table('careers_submissions').select('name,phone,email,city_state,role,created_at').not_.is_('phone','null').neq('phone','').execute()
for r in result.data:
    print(json.dumps(r))
