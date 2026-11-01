
import json
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]

DATA_FILE = BASE_DIR / "starter-pack" / "CrunchWeek_StarterPack" / "data" / "cit_public.json"
SCHEDULE_FILE = BASE_DIR / "exports" / "schedule_baseline_98.json"

with open(DATA_FILE, encoding="utf-8") as f:
    dataset = json.load(f)

with open(SCHEDULE_FILE, encoding="utf-8") as f:
    schedule = json.load(f)

# Dataset sessions are indexed by their "id".
session_data = {
    session["id"]: session
    for session in dataset["sessions"]
}

occupied = defaultdict(set)

for assignment in schedule["sessions"]:
    session_id = assignment["session_id"]

    if session_id not in session_data:
        print("WARNING: Unknown session:", session_id)
        continue

    session = session_data[session_id]

    day = assignment["day"]
    start = assignment["start_period"]
    duration = session["duration"]
    end = start + duration - 1

    # Actual dataset field is "groups".
    for group in session["groups"]:
        for period in range(start, end + 1):
            occupied[(group, day)].add(period)

results = []

for (group, day), periods in occupied.items():
    first = min(periods)
    last = max(periods)

    gaps = [
        p for p in range(first, last + 1)
        if p not in periods
    ]

    if gaps:
        results.append({
            "group": group,
            "day": day,
            "gaps": len(gaps),
            "gap_periods": gaps,
            "occupied_periods": sorted(periods)
        })

results.sort(key=lambda x: x["gaps"], reverse=True)

print("\n========== S2 GAP DIAGNOSTIC ==========\n")

total = 0

for item in results:
    total += item["gaps"]

    print(
        f"Group: {item['group']} | "
        f"Day: {item['day']} | "
        f"Gaps: {item['gaps']} | "
        f"Gap periods: {item['gap_periods']} | "
        f"Occupied: {item['occupied_periods']}"
    )

print("\nTotal calculated gaps:", total)
print("=======================================\n")
