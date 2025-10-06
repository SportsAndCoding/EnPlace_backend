import json
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

# Load the generated shifts data
with open('generated_shifts.json', 'r') as f:
    shifts = json.load(f)

# Convert to DataFrame
df = pd.DataFrame(shifts)
df['date'] = pd.to_datetime(df['date'])
df['hour'] = df['start_time'].str.split(':').str[0].astype(int)
df['day_of_week'] = df['date'].dt.day_name()

print("=" * 80)
print("SCHEDULE ANALYSIS - EXPLORATORY DATA ANALYSIS")
print("=" * 80)

# 1. Basic Stats
print("\n1. BASIC STATISTICS")
print("-" * 80)
print(f"Total shifts generated: {len(df)}")
print(f"Unique staff scheduled: {df['staff_id'].nunique()}")
print(f"Date range: {df['date'].min()} to {df['date'].max()}")
print(f"Days covered: {df['date'].nunique()}")
print(f"Positions scheduled: {df['position'].unique()}")

# 2. Staff Utilization
print("\n2. STAFF UTILIZATION")
print("-" * 80)
staff_hours = df.groupby('staff_id').size().sort_values(ascending=False)
print(f"\nTop 10 most scheduled staff:")
print(staff_hours.head(10))
print(f"\nBottom 10 least scheduled staff:")
print(staff_hours.tail(10))
print(f"\nAverage hours per staff: {staff_hours.mean():.1f}")
print(f"Max hours for any staff: {staff_hours.max()}")
print(f"Min hours for any staff: {staff_hours.min()}")

# RED FLAG: Anyone scheduled > 80 hours for 2 weeks?
overworked = staff_hours[staff_hours > 80]
if len(overworked) > 0:
    print(f"\n🚨 RED FLAG: {len(overworked)} staff scheduled over 80 hours (2-week period)")
    print(overworked)

# 3. Position Distribution
print("\n3. POSITION DISTRIBUTION")
print("-" * 80)
position_counts = df['position'].value_counts()
print(position_counts)
print(f"\nPosition percentages:")
print((position_counts / len(df) * 100).round(1))

# 4. Hourly Distribution
print("\n4. HOURLY COVERAGE")
print("-" * 80)
hourly = df.groupby('hour').size().sort_index()
print(hourly)

# RED FLAG: Check for weird hours
if hourly.index.min() < 9 or hourly.index.max() > 23:
    print(f"\n🚨 RED FLAG: Shifts outside 9AM-11PM detected")

# 5. Daily Coverage
print("\n5. DAILY COVERAGE")
print("-" * 80)
daily = df.groupby('date').size().sort_index()
print(daily)

# RED FLAG: Huge variance in daily coverage?
if daily.std() / daily.mean() > 0.3:
    print(f"\n⚠️  WARNING: High variance in daily coverage (std: {daily.std():.1f}, mean: {daily.mean():.1f})")

# 6. Peak Hour Analysis
print("\n6. PEAK HOUR ANALYSIS (6PM-9PM)")
print("-" * 80)
peak_hours = df[df['hour'].isin([18, 19, 20])]
peak_by_position = peak_hours.groupby('position').size()
print("Staff scheduled during peak hours by position:")
print(peak_by_position)

# 7. Weekend vs Weekday
print("\n7. WEEKEND VS WEEKDAY")
print("-" * 80)
df['is_weekend'] = df['day_of_week'].isin(['Saturday', 'Sunday'])
weekend_weekday = df.groupby('is_weekend')['position'].value_counts()
print(weekend_weekday)

# 8. Consecutive Shift Check
print("\n8. CONSECUTIVE SHIFT DETECTION")
print("-" * 80)
df_sorted = df.sort_values(['staff_id', 'date', 'hour'])
consecutive_violations = []

for staff_id in df['staff_id'].unique():
    staff_shifts = df_sorted[df_sorted['staff_id'] == staff_id]
    prev_date = None
    prev_hour = None
    consecutive_count = 1
    
    for _, shift in staff_shifts.iterrows():
        if prev_date == shift['date'] and prev_hour == shift['hour'] - 1:
            consecutive_count += 1
            if consecutive_count > 10:  # More than 10 hours straight
                consecutive_violations.append({
                    'staff_id': staff_id,
                    'date': shift['date'],
                    'consecutive_hours': consecutive_count
                })
        else:
            consecutive_count = 1
        
        prev_date = shift['date']
        prev_hour = shift['hour']

if consecutive_violations:
    print(f"🚨 RED FLAG: {len(consecutive_violations)} instances of staff scheduled >10 consecutive hours")
    for v in consecutive_violations[:5]:
        print(f"  {v['staff_id']} on {v['date']}: {v['consecutive_hours']} hours straight")

# 9. Cost Effectiveness Check
print("\n9. EFFICIENCY ANALYSIS")
print("-" * 80)
df['confidence_score'] = df['confidence_score'].astype(float)
avg_efficiency = df['confidence_score'].mean()
print(f"Average confidence score (efficiency): {avg_efficiency:.2f}")
print(f"Min efficiency: {df['confidence_score'].min():.2f}")
print(f"Max efficiency: {df['confidence_score'].max():.2f}")

# By position
efficiency_by_position = df.groupby('position')['confidence_score'].mean().sort_values(ascending=False)
print(f"\nAverage efficiency by position:")
print(efficiency_by_position)

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)