from database.supabase_client import get_supabase
import csv
import io

supabase = get_supabase()
result = supabase.table('careers_submissions').select('name,phone,email,city_state,role,created_at').not_.is_('phone','null').neq('phone','').execute()

# Restaurant keywords
rest_kw = ['server','bartender','cook','chef','host','busser','barback','waiter','waitress','manager','kitchen','food','beverage','restaurant','dining','barista','cashier','expo','runner','catering','banquet','cocktail','sous','grill','prep','foh','boh','shift lead','deli','sandwich','trainer','supervisor','owner','operator','mixologist','culinary','hospitality','inshop','crew','dietary','captain','bistro']
skip_kw = ['hvac','electrician','trooper','massage therapist','optometric','medical assistant','patient access','criminal justice','cosmetologist','budtender','hair stylist','public transportation','personal care','aquatics','data entry','tutor','social media manager','cleaner','lead technician','recovery associate','brand ambassador','apprentice electrician','timeshare','passenger service','school supervisor']

def is_rest(role):
    if not role or role == 'null': return False
    rl = role.lower()
    for n in skip_kw:
        if n in rl: return False
    for r in rest_kw:
        if r in rl: return True
    return False

def get_tier(role):
    if not role: return 4
    rl = role.lower()
    if any(k in rl for k in ['owner','operator','director','regional']): return 1
    if any(k in rl for k in ['general manager','restaurant manager','kitchen manager','bar manager','executive chef']): return 2
    if any(k in rl for k in ['manager','supervisor','lead','trainer','captain','head chef','sous chef']): return 3
    return 4

tier_labels = {1:'DECISION_MAKER', 2:'GM_LEVEL', 3:'MANAGEMENT', 4:'STAFF'}

filtered = [r for r in result.data if is_rest(r.get('role',''))]
filtered.sort(key=lambda x: get_tier(x.get('role','')))

print("Tier|First_Name|Full_Name|Phone|Email|Role|City_State|Email_Type")
for r in filtered:
    t = get_tier(r.get('role',''))
    name = r.get('name','').strip()
    first = name.split()[0].title() if name else ''
    email = r.get('email','')
    et = 'INDEED' if 'indeedemail' in email else ('NONE' if 'placeholder' in email else 'DIRECT')
    phone = r.get('phone','')
    role = r.get('role','')
    city = r.get('city_state','') or ''
    print(f"{tier_labels[t]}|{first}|{name}|{phone}|{email}|{role}|{city}|{et}")

print(f"\n--- TOTAL RESTAURANT: {len(filtered)} of {len(result.data)} ---")
t_counts = {}
for r in filtered:
    t = get_tier(r.get('role',''))
    t_counts[t] = t_counts.get(t,0) + 1
for t in sorted(t_counts):
    print(f"  {tier_labels[t]}: {t_counts[t]}")