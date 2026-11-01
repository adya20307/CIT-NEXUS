# Crunch Week starter pack

Everything you need to build against the Chilika Institute of Technology (CIT) dataset. Read the problem statement first; this file explains the data and the export format.

```
data/cit_public.json          the full dataset (one file, everything in it)
data/csv/*.csv                the same data as spreadsheets, for a quick look
validator.py                  the official validator (Python 3.8+, no installs)
examples/schedule.example.json   shape of the file you export (not a real solution)
examples/sample_disruption.json  practice disruption, same format as the live one
examples/sample_requests.md      plain-language requests to test your AI layer
```

Your app should read this file and change files in the format below, not hard-code anything from them. Judges will reset your app, generate the timetable themselves and apply change files of their own.

## Quick facts about the dataset

| Thing | Count |
| --- | --- |
| Rooms | 27 (classrooms, lecture halls, auditorium, seminar halls, open-air theatre, 7 labs) |
| Faculty | 40 |
| Student groups | 14 (Year 1 sections A to D, Year 2 and Year 3 for 5 branches) |
| Courses | 62 |
| Sessions to schedule | 189 (243 teaching hours a week) |
| Spectra week booking requests | 25 (one of them is cancelled) |
| Midsem exams | 41 |

A timetable with zero hard violations exists for this dataset. We checked.

## Time model

- Days: `MON` to `FRI`.
- Periods `1` to `9` are teaching periods, one hour each: P1 08:00, P2 09:00 ... P5 12:00, lunch 13:00 to 14:00, P6 14:00 ... P9 17:00.
- Periods `10` to `13` (18:00 to 22:00) are for events only.
- A session with `duration` d placed at `start_period` p occupies p, p+1, ... p+d-1. It may not span P5 and P6 (lunch).
- Labs (`kind: "lab"`, 3 hours) start only at P2 or P6.

## The dataset, field by field

### rooms
`id`, `name`, `building`, `type`, `capacity`, `features`, `accessible`, `allowed_uses`.

Room `type` is one of `classroom`, `lecture_hall`, `auditorium`, `seminar_hall`, `outdoor`, `lab_cs`, `lab_elec`, `lab_phy`, `workshop`, `lab_civil`. `allowed_uses` says whether a room can take lectures, tutorials, labs, exams or events. Seminar halls and the open-air theatre never take regular classes.

### room_unavailability
`room_id`, `day`, `periods`, `reason`. The room can't be used at those times.

### faculty
`id`, `name`, `department`, `max_hours_per_day` (hard limit), `needs_accessible`, `unavailable` (list of `day` + `periods`, hard), `preferences` (soft).

A preference looks like `{"type": "avoid", "days": [...], "periods": [...], "note": "..."}`. Each hour a faculty member teaches inside an avoided day and period costs 2 points (S1).

### student_groups
`id`, `name`, `year`, `department`, `size`, `needs_accessible`.

### courses
Reference information: `lecture_hours`, `tutorial_hours`, `lab_blocks` per week. You don't schedule courses; you schedule sessions.

### sessions — the thing you schedule
```json
{
  "id": "MA101-AB-L1",
  "course_id": "MA101",
  "kind": "lecture",               // lecture | tutorial | lab
  "groups": ["Y1-A", "Y1-B"],      // every group that must attend
  "faculty_id": "F19",
  "size": 180,                     // students attending
  "duration": 1,                   // periods
  "room_types": ["classroom", "lecture_hall", "auditorium"],
  "needs_accessible": false,
  "basket_slot": null,             // e.g. "OEB-1" for open electives
  "fixed_slot": null               // e.g. {"day": "THU", "start_period": 6} = must go exactly there
}
```
Each session needs exactly one `day`, `start_period` and `room_id`. Faculty are already assigned.

**Elective baskets.** Year 3 students each take one of three open electives (OE301, OE302, OE303). The three courses run in parallel: every session with the same `basket_slot` must be at the same day and period, in different rooms. Their `groups` lists all Year 3 groups because each group has students in all three. Sessions in the same basket slot don't clash with each other for groups; they do clash with everything else those groups have.

### booking_requests — Spectra week (Week 7)
```json
{
  "id": "BR-07",
  "submitted_at": "2027-01-26T09:45:00+05:30",
  "requester": "Dramatics Society",
  "role": "club",                         // admin | department_coordinator | faculty | club
  "requested_category": "club_event",
  "title": "Stage play: Andha Yug",
  "attendance": 130,
  "duration_periods": 3,
  "windows": [{"day": "THU", "from_period": 10, "to_period": 12}],
  "room_types": ["seminar_hall", "auditorium"],
  "needs": ["stage", "pa_system"],        // "accessible" means the room must be accessible
  "requested_room": "SH-1",               // a wish, may be null, may not even exist
  "mandatory_for_groups": [],             // these groups must be free
  "series_id": null,
  "raw_text": "Dramatics needs SH-1 Thursday 6 to 9 pm ..."
}
```
A booking must fit fully inside one window to be approved. `raw_text` is the original message, so it doubles as test input for your plain-language parser.

**Category is capped by role.** `role_category_cap` in the dataset says the highest category each role may claim. A club asking for `institute_event` is still a `club_event`. Priority order is in `priority_order`; ties go to the earlier `submitted_at`.

**cancellations** lists requests withdrawn after submission. Never approve those.

### exams
`id`, `course_ids`, `groups`, `size`, `duration_hours`. Exam week is `MON` to `SAT` with sessions `AM` (09:30 to 11:30) and `PM` (14:00 to 16:00), in `calendar.exam_week`. Rooms seat half their capacity during exams; an exam may use several rooms.

## What you export: schedule.json

```json
{
  "team": "Your team name",
  "dataset_id": "cit-public-v1",
  "sessions": [
    {"session_id": "MA101-AB-L1", "day": "MON", "start_period": 2, "room_id": "LH-1"}
  ],
  "fest_week": {
    "bookings": [
      {"request_id": "BR-01", "status": "approved", "day": "MON",
       "start_period": 4, "end_period": 5, "room_id": "AUD", "reason": "Institute event"},
      {"request_id": "BR-09", "status": "rejected", "reason": "No venue seats 1,200"},
      {"request_id": "BR-07", "status": "alternative_offered", "day": "THU",
       "start_period": 10, "end_period": 12, "room_id": "AUD", "reason": "SH-1 taken by an earlier request"}
    ],
    "session_overrides": [
      {"session_id": "ID101-ALL-L1", "day": "WED", "start_period": 4, "room_id": "AUD", "displaced_by": "BR-01"}
    ]
  },
  "exams": [
    {"exam_id": "EX-MA101", "day": "MON", "session": "AM", "rooms": ["AUD", "LH-1", "LH-2"]}
  ]
}
```

- `end_period` is inclusive.
- `status` is `approved`, `rejected` or `alternative_offered`. Rejections and alternatives need a `reason`. An alternative may sit outside the requested window; it is half-scored.
- `session_overrides` move a session for Week 7 only. Only an approved institute event may displace a regular session, and the session must be moved, not dropped.
- `exams` is required in `schedule.json`. A missing exam schedule counts every exam as unscheduled. In `schedule_after.json` (after a disruption) it's optional.

## Running the validator

```
python validator.py --data data/cit_public.json --schedule schedule.json
python validator.py --data data/cit_public.json --schedule schedule.json --json report.json
```

Checking a change: apply `examples/sample_disruption.json` in your app, export the repaired timetable, then

```
python validator.py --data data/cit_public.json --schedule schedule_after.json \
                    --disruption examples/sample_disruption.json --baseline schedule.json
```

It prints hard violations by rule, the soft penalty (S1 to S6), the booking score, priority inversions (you rejected someone who should have won a room), and how many sessions moved.

### Disruption file format
```json
{"changes": [
  {"type": "faculty_unavailable", "faculty_id": "F07", "days": ["MON"], "periods": [1,2,3,4,5,6,7,8,9], "reason": "..."},
  {"type": "room_unavailable", "room_id": "CR-102", "days": ["MON","TUE"], "periods": [1,2,3], "reason": "..."},
  {"type": "add_faculty", "faculty": { ...same shape as a faculty entry... }},
  {"type": "add_session", "session": { ...same shape as a session, may have fixed_slot... }},
  {"type": "remove_session", "session_id": "HS201-H2-L2"}
]}
```
Changes are permanent: they hold for every week after they're applied.

If you think the validator is wrong, tell the organisers during the hackathon.

## What goes in your exports/ folder

Commit both of these to your repo before the deadline:

- `exports/schedule.json`: your app's output for `cit_public.json` (timetable, exams and Spectra week bookings).
- `exports/schedule_after.json`: the timetable after applying `examples/sample_disruption.json` in your app, starting from that `schedule.json`.

Both must be produced by the app. Files made or edited by hand lead to disqualification.
