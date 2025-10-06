import json
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime

# Load data
with open('generated_shifts.json', 'r') as f:
    shifts = json.load(f)

df = pd.DataFrame(shifts)
df['date'] = pd.to_datetime(df['date'])
df['hour'] = df['start_time'].str.split(':').str[0].astype(int)
df['day_of_week'] = df['date'].dt.day_name()
df['confidence_score'] = df['confidence_score'].astype(float)

# Set style
sns.set_style("whitegrid")
fig, axes = plt.subplots(3, 2, figsize=(16, 12))
fig.suptitle('Schedule Optimization Analysis', fontsize=16, fontweight='bold')

# 1. Hourly heatmap
hourly_coverage = df.groupby(['date', 'hour']).size().unstack(fill_value=0)
sns.heatmap(hourly_coverage, cmap='YlOrRd', ax=axes[0, 0], cbar_kws={'label': 'Staff Count'})
axes[0, 0].set_title('Hourly Coverage Heatmap')
axes[0, 0].set_xlabel('Hour of Day')
axes[0, 0].set_ylabel('Date')

# 2. Position distribution
position_counts = df['position'].value_counts()
axes[0, 1].bar(range(len(position_counts)), position_counts.values)
axes[0, 1].set_xticks(range(len(position_counts)))
axes[0, 1].set_xticklabels(position_counts.index, rotation=45, ha='right')
axes[0, 1].set_title('Shifts by Position')
axes[0, 1].set_ylabel('Number of Shifts')

# 3. Staff utilization distribution
staff_hours = df.groupby('staff_id').size()
axes[1, 0].hist(staff_hours, bins=20, edgecolor='black')
axes[1, 0].axvline(staff_hours.mean(), color='red', linestyle='--', label=f'Mean: {staff_hours.mean():.1f}')
axes[1, 0].axvline(40, color='orange', linestyle='--', label='40 hrs (1 week)')
axes[1, 0].axvline(80, color='green', linestyle='--', label='80 hrs (2 weeks)')
axes[1, 0].set_title('Staff Hour Distribution')
axes[1, 0].set_xlabel('Hours Scheduled')
axes[1, 0].set_ylabel('Number of Staff')
axes[1, 0].legend()

# 4. Hourly coverage by position
hourly_by_position = df.groupby(['hour', 'position']).size().unstack(fill_value=0)
hourly_by_position.plot(kind='area', stacked=True, ax=axes[1, 1], alpha=0.7)
axes[1, 1].set_title('Hourly Coverage by Position (Stacked)')
axes[1, 1].set_xlabel('Hour of Day')
axes[1, 1].set_ylabel('Staff Count')
axes[1, 1].legend(title='Position', bbox_to_anchor=(1.05, 1), loc='upper left')

# 5. Daily coverage
daily_coverage = df.groupby('date').size()
axes[2, 0].plot(daily_coverage.index, daily_coverage.values, marker='o')
axes[2, 0].set_title('Daily Total Coverage')
axes[2, 0].set_xlabel('Date')
axes[2, 0].set_ylabel('Total Shifts')
axes[2, 0].tick_params(axis='x', rotation=45)
axes[2, 0].grid(True, alpha=0.3)

# 6. Efficiency distribution
axes[2, 1].hist(df['confidence_score'], bins=20, edgecolor='black', alpha=0.7)
axes[2, 1].axvline(df['confidence_score'].mean(), color='red', linestyle='--', 
                   label=f'Mean: {df["confidence_score"].mean():.2f}')
axes[2, 1].set_title('Efficiency Multiplier Distribution')
axes[2, 1].set_xlabel('Confidence Score (Efficiency)')
axes[2, 1].set_ylabel('Frequency')
axes[2, 1].legend()

plt.tight_layout()
plt.savefig('schedule_analysis.png', dpi=300, bbox_inches='tight')
print("✅ Visualization saved as 'schedule_analysis.png'")
plt.show()