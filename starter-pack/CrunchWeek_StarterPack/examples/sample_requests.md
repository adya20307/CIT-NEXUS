# Sample plain-language requests (public)

Practice inputs for your AI layer. Every `raw_text` field in the dataset's booking requests is also a test input.
The judges use a different, harder set when they test your app.

For each one, your app should produce an editable structured booking, or ask a follow-up question when something essential is missing.

1. Need a room for 45 people on Tuesday 6 to 8 pm, projector please.
2. Book the auditorium for the CSE department farewell, Friday evening, around 400 people, need stage and mic.
3. Can I get a lab with computers for a 3-hour coding contest on Thursday afternoon? About 60 participants.
4. Our club meets every Monday and Wednesday 7 to 8 pm, 30 people, any small room is fine.
5. Mechanical third years need a 2 hour session with an industry speaker on Wednesday, it's compulsory for them.
6. seminar hall kal shaam 6 baje, 2 ghante, 100 log, mic chahiye
7. I want SH-2 on Friday at 10 am for a wheelchair-accessible poster session, 60 people.
8. Need somewhere for the quiz finals after lunch on Thursday, maybe 150 people.

### Expected structure for three of them

Request 1:
```json
{"attendance": 45, "duration_periods": 2,
 "windows": [{"day": "TUE", "from_period": 10, "to_period": 11}],
 "needs": ["projector"], "room_types": ["classroom", "lecture_hall", "seminar_hall"],
 "mandatory_for_groups": [], "follow_up": null}
```

Request 6 ("seminar hall tomorrow 6 pm, 2 hours, 100 people, need a mic"):
```json
{"attendance": 100, "duration_periods": 2,
 "windows": [{"day": "<tomorrow's weekday>", "from_period": 10, "to_period": 11}],
 "needs": ["pa_system"], "room_types": ["seminar_hall"], "follow_up": null}
```
"Kal" can mean yesterday or tomorrow in Hindi. In a booking request it means tomorrow, and the app should resolve it to a weekday.

Request 7: SH-2 is not wheelchair accessible. A good system notices the conflict between the named room and the stated need, and offers SH-1 instead of silently booking SH-2.

Request 8 is vague about time and size. "After lunch" could be P6 onward. A good system either asks, or proposes a window and makes the assumption visible in the editable form.
