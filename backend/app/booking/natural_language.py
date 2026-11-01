import re
from datetime import date, datetime, timedelta, timezone


HINGLISH_MARKERS = {
    "aaj",
    "kal",
    "parso",
    "chahiye",
    "karo",
    "khali",
    "khaali",
    "kitne",
    "baje",
    "ghanta",
    "ghante",
    "log",
    "liya",
    "liye",
    "se",
}

DAY_NAMES = {
    "monday": "MON",
    "mondays": "MON",
    "mon": "MON",
    "somvaar": "MON",
    "somwar": "MON",
    "tuesday": "TUE",
    "tuesdays": "TUE",
    "tue": "TUE",
    "mangalvaar": "TUE",
    "mangalwar": "TUE",
    "wednesday": "WED",
    "wednesdays": "WED",
    "wed": "WED",
    "budhvaar": "WED",
    "budhwar": "WED",
    "thursday": "THU",
    "thursdays": "THU",
    "thu": "THU",
    "guruvaar": "THU",
    "guruwaar": "THU",
    "guruwar": "THU",
    "friday": "FRI",
    "fridays": "FRI",
    "fri": "FRI",
    "shukravaar": "FRI",
    "shukrawar": "FRI",
    "saturday": "SAT",
    "sat": "SAT",
    "shanivaar": "SAT",
    "shaniwar": "SAT",
    "sunday": "SUN",
    "sun": "SUN",
    "ravivaar": "SUN",
    "raviwar": "SUN",
}

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

ROOM_TYPE_PATTERNS = (
    (("computer lab", "cs lab", "coding lab", "lab with computers"), ["lab_cs"]),
    (("physics lab", "phy lab"), ["lab_phy"]),
    (("electrical lab", "electronics lab", "elec lab"), ["lab_elec"]),
    (("civil lab",), ["lab_civil"]),
    (("workshop", "mechanical workshop"), ["workshop"]),
    (("seminar hall", "seminar room", "sh-1", "sh-2"), ["seminar_hall"]),
    (("lecture hall", "lecture theatre"), ["lecture_hall"]),
    (("auditorium", "main hall"), ["auditorium"]),
    (("classroom", "class room"), ["classroom"]),
    (("outdoor", "open air", "open-air"), ["outdoor"]),
    (("lab", "laboratory"), ["lab_cs", "lab_elec", "lab_phy", "lab_civil"]),
)

DEFAULT_EVENT_ROOM_TYPES = [
    "classroom",
    "lecture_hall",
    "seminar_hall",
]

TIME_TOKEN = r"(?:[01]?\d|2[0-3])(?::[0-5]\d)?"
MERIDIEM = r"(?:a\.?m\.?|p\.?m\.?)"
TIME_RANGE_RE = re.compile(
    rf"\b(?:from\s+|between\s+)?(?P<start>{TIME_TOKEN})\s*"
    rf"(?P<start_meridiem>{MERIDIEM})?\s*"
    rf"(?:to|until|till|se|[-–—])\s*"
    rf"(?P<end>{TIME_TOKEN})\s*(?P<end_meridiem>{MERIDIEM})?\b",
    re.IGNORECASE,
)
SINGLE_TIME_RE = re.compile(
    rf"\b(?:(?P<prefix>at|around|near|shaam|sham|evening|subah|morning)\s+)?"
    rf"(?<![a-z0-9-])(?P<hour>{TIME_TOKEN})\s*(?P<meridiem>{MERIDIEM})?"
    rf"(?P<time_suffix>\s*(?:baje|o['’]?clock))?\b",
    re.IGNORECASE,
)


def detect_language(text):
    normalized = text.lower()
    if any(
        re.search(rf"\b{re.escape(marker)}\b", normalized)
        for marker in HINGLISH_MARKERS
    ):
        return "hinglish"
    if re.search(r"\b(?:ke\s+liye|karna\s+hai|karo|hai\s+kya)\b", normalized):
        return "hinglish"
    return "english"


def _clean_meridiem(value):
    if not value:
        return None
    return "pm" if value.lower().startswith("p") else "am"


def _hour_minutes(value, meridiem):
    hour_text, separator, minute_text = value.partition(":")
    hour = int(hour_text)
    minute = int(minute_text) if separator else 0
    if minute >= 60:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif hour > 23:
        return None
    return hour * 60 + minute


def _parse_clock_range(match):
    start_text = match.group("start")
    end_text = match.group("end")
    start_meridiem = _clean_meridiem(match.group("start_meridiem"))
    end_meridiem = _clean_meridiem(match.group("end_meridiem"))
    start_has_meridiem = start_meridiem is not None
    end_has_meridiem = end_meridiem is not None
    start_raw = int(start_text.split(":")[0])
    end_raw = int(end_text.split(":")[0])

    if not start_has_meridiem and not end_has_meridiem:
        if start_raw <= 12 or end_raw <= 12:
            return None, "meridiem"
        start = _hour_minutes(start_text, None)
        end = _hour_minutes(end_text, None)
    elif start_has_meridiem and not end_has_meridiem:
        start = _hour_minutes(start_text, start_meridiem)
        end = _hour_minutes(end_text, start_meridiem)
        if start is not None and end is not None and end <= start and end_raw < 12:
            end = _hour_minutes(end_text, "pm" if start_meridiem == "am" else "am")
    elif end_has_meridiem and not start_has_meridiem:
        end = _hour_minutes(end_text, end_meridiem)
        start = _hour_minutes(start_text, end_meridiem)
        if start is not None and end is not None and start >= end and start_raw < 12:
            start = _hour_minutes(
                start_text,
                "am" if end_meridiem == "pm" else "pm",
            )
    else:
        start = _hour_minutes(start_text, start_meridiem)
        end = _hour_minutes(end_text, end_meridiem)

    if start is None or end is None or end <= start:
        return None, "invalid_range"
    return (start, end), None


def _parse_time(text):
    normalized_text = re.sub(
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b",
        " ",
        text,
    )
    match = TIME_RANGE_RE.search(normalized_text)
    if match:
        clock_range, error = _parse_clock_range(match)
        return {
            "range": clock_range,
            "start": None,
            "error": error,
            "match": match.group(0),
        }

    # Avoid interpreting dates, group IDs and capacity values as times.
    match = SINGLE_TIME_RE.search(normalized_text)
    if not match or not any(
        match.group(name)
        for name in ("prefix", "meridiem", "time_suffix")
    ):
        return {"range": None, "start": None, "error": None, "match": None}

    hour_text = match.group("hour")
    meridiem = _clean_meridiem(match.group("meridiem"))
    prefix = (match.group("prefix") or "").lower()
    if not meridiem and prefix in ("shaam", "sham", "evening"):
        meridiem = "pm"
    elif not meridiem and prefix == "morning":
        meridiem = "am"
    elif not meridiem and re.search(r"\bshaam\b", text):
        meridiem = "pm"
    elif not meridiem and re.search(r"\bsubah\b", text):
        meridiem = "am"
    has_24_hour_minutes = ":" in hour_text
    if (
        not meridiem
        and not has_24_hour_minutes
        and int(hour_text.split(":")[0]) <= 12
    ):
        return {
            "range": None,
            "start": None,
            "error": "meridiem",
            "match": match.group(0),
        }
    minutes = _hour_minutes(hour_text, meridiem)
    if minutes is None:
        return {
            "range": None,
            "start": None,
            "error": "invalid_time",
            "match": match.group(0),
        }
    return {"range": None, "start": minutes, "error": None, "match": match.group(0)}


def _past_context(text):
    return bool(re.search(
        r"\b(?:was|were|had|booked|happened|held|tha|thi|the|kiya|kiye|"
        r"ki\s+thi|ho\s+gaya|ho\s+gayi)\b",
        text,
    ))


def _parse_date(text, today):
    lower = text.lower()
    past = _past_context(lower)

    date_patterns = (
        (re.compile(r"\b(?P<year>20\d{2})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b"), "ymd"),
        (re.compile(r"\b(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>20\d{2})\b"), "dmy"),
        (re.compile(r"\b(?P<day>\d{1,2})-(?P<month>\d{1,2})-(?P<year>20\d{2})\b"), "dmy"),
    )
    for pattern, _ in date_patterns:
        match = pattern.search(lower)
        if match:
            try:
                date_value = date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError:
                return {"date": None, "day": None, "error": "invalid_date"}
            return {
                "date": date_value,
                "day": date_value.strftime("%a").upper(),
                "days": [date_value.strftime("%a").upper()],
                "error": None,
            }

    month_names = sorted(MONTHS, key=len, reverse=True)
    month_pattern = re.compile(
        r"\b(?:(?P<day_first>\d{1,2})\s+)?"
        r"(?P<month_name>" + "|".join(month_names) + r")"
        r"(?:\s+(?P<day_last>\d{1,2}))?"
        r"(?:,?\s+(?P<year>20\d{2}))?\b",
        re.IGNORECASE,
    )
    month_match = month_pattern.search(lower)
    if month_match:
        day_value = month_match.group("day_first") or month_match.group("day_last")
        year_value = month_match.group("year")
        if not day_value:
            return {"date": None, "day": None, "error": "date_day_missing"}
        if not year_value:
            return {"date": None, "day": None, "error": "date_year_missing"}
        try:
            date_value = date(
                int(year_value),
                MONTHS[month_match.group("month_name").lower()],
                int(day_value),
            )
        except ValueError:
            return {"date": None, "day": None, "error": "invalid_date"}
        return {
            "date": date_value,
            "day": date_value.strftime("%a").upper(),
            "days": [date_value.strftime("%a").upper()],
            "error": None,
        }

    numeric_date_without_year = re.search(
        r"\b(?P<first>\d{1,2})/(?P<second>\d{1,2})(?!/\d)",
        lower,
    )
    if numeric_date_without_year:
        return {"date": None, "day": None, "error": "date_year_missing"}

    if re.search(r"\bday\s+before\s+yesterday\b", lower):
        date_value = today - timedelta(days=2)
        day = date_value.strftime("%a").upper()
        return {"date": date_value, "day": day, "days": [day], "error": None}
    if re.search(r"\bday\s+after\s+tomorrow\b", lower):
        date_value = today + timedelta(days=2)
        day = date_value.strftime("%a").upper()
        return {"date": date_value, "day": day, "days": [day], "error": None}

    if re.search(r"\b(?:yesterday|kal)\b", lower) and past:
        date_value = today - timedelta(days=1)
        day = date_value.strftime("%a").upper()
        return {"date": date_value, "day": day, "days": [day], "error": None}
    if re.search(r"\b(?:tomorrow|kal)\b", lower):
        date_value = today + timedelta(days=1)
        day = date_value.strftime("%a").upper()
        return {"date": date_value, "day": day, "days": [day], "error": None}
    if re.search(r"\bparso\b", lower) and past:
        date_value = today - timedelta(days=2)
        day = date_value.strftime("%a").upper()
        return {"date": date_value, "day": day, "days": [day], "error": None}
    if re.search(r"\b(?:parso|day after tomorrow)\b", lower):
        date_value = today + timedelta(days=2)
        day = date_value.strftime("%a").upper()
        return {"date": date_value, "day": day, "days": [day], "error": None}
    if re.search(r"\byesterday\b", lower):
        date_value = today - timedelta(days=1)
        day = date_value.strftime("%a").upper()
        return {"date": date_value, "day": day, "days": [day], "error": None}
    if re.search(r"\baaj\b|\btoday\b", lower):
        day = today.strftime("%a").upper()
        return {"date": today, "day": day, "days": [day], "error": None}

    tokens = re.findall(r"[a-z]+", lower)
    weekdays = list(dict.fromkeys(
        DAY_NAMES[token]
        for token in tokens
        if token in DAY_NAMES
    ))
    if weekdays:
        if len(weekdays) == 1 and "last" in tokens:
            day_name = weekdays[0]
            target_index = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].index(day_name)
            days_ago = (today.weekday() - target_index) % 7 or 7
            date_value = today - timedelta(days=days_ago)
            return {
                "date": date_value,
                "day": day_name,
                "days": [day_name],
                "error": None,
            }
        if len(weekdays) == 1 and "next" in tokens:
            day_name = weekdays[0]
            target_index = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].index(day_name)
            days_ahead = (target_index - today.weekday()) % 7 or 7
            date_value = today + timedelta(days=days_ahead)
            return {
                "date": date_value,
                "day": day_name,
                "days": [day_name],
                "error": None,
            }
        return {
            "date": None,
            "day": weekdays[0],
            "days": weekdays,
            "error": None,
        }

    for index, token in enumerate(tokens):
        if token not in DAY_NAMES:
            continue
        day_name = DAY_NAMES[token]
        if index > 0 and tokens[index - 1] == "next":
            target_index = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].index(day_name)
            days_ahead = (target_index - today.weekday()) % 7 or 7
            date_value = today + timedelta(days=days_ahead)
            return {
                "date": date_value,
                "day": day_name,
                "days": [day_name],
                "error": None,
            }
        return {
            "date": None,
            "day": day_name,
            "days": [day_name],
            "error": None,
        }

    return {"date": None, "day": None, "days": [], "error": "date_missing"}


def _parse_duration(text):
    number_words = {
        "a": 1,
        "an": 1,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
    }
    match = re.search(
        r"\b(?:for\s+|duration(?:\s+of)?\s+|lasting\s+)?"
        r"(?P<amount>\d+(?:\.\d+)?|one|two|three|four|five|a|an)\s*"
        r"[- ]*(?P<unit>hours?|hrs?|ghante|ghanta|minutes?|mins?)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    raw_amount = match.group("amount").lower()
    amount = float(number_words[raw_amount]) if raw_amount in number_words else float(raw_amount)
    unit = match.group("unit").lower()
    hours = amount / 60 if unit.startswith(("minute", "min")) else amount
    if hours <= 0 or not hours.is_integer():
        return None, "duration_must_be_whole_periods"
    return int(hours), None


def _parse_attendance(text):
    match = re.search(
        r"\b(?P<count>\d{1,4})\s*"
        r"(?:students?|people|persons?|log|participants?|attendees?)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"\b(?:capacity(?:\s+for)?|attendance(?:\s+of)?|around|about|"
            r"approximately|approx\.?)\s+(?P<count>\d{1,4})\b"
            r"(?!\s*(?:hours?|hrs?|ghante|ghanta|minutes?|mins?))",
            text,
            re.IGNORECASE,
        )
    return int(match.group("count")) if match else None


def _parse_room(data, lower):
    rooms = data["rooms"]
    for room in sorted(rooms, key=lambda item: len(item["id"]), reverse=True):
        if re.search(
            rf"(?<![a-z0-9]){re.escape(room['id'].lower())}(?![a-z0-9])",
            lower,
        ):
            return [room["type"]], room["id"]

    for phrases, types in ROOM_TYPE_PATTERNS:
        if any(phrase in lower for phrase in phrases):
            return list(types), None
    if re.search(r"\b(?:any|small)\s+room\b|\ba\s+room\b|\broom\b", lower):
        return list(DEFAULT_EVENT_ROOM_TYPES), None
    return [], None


def _parse_needs(text):
    lower = text.lower()
    needs = []
    if re.search(r"\b(?:projector|projector\s+please)\b", lower):
        needs.append("projector")
    if re.search(r"\b(?:mic|microphone|sound|pa\s+system)\b", lower):
        needs.append("pa_system")
    if re.search(r"\bstage\b", lower):
        needs.append("stage")
    if re.search(r"\b(?:computer|computers|coding|computerized)\b", lower):
        needs.append("computers")
    if re.search(
        r"\b(?:wheelchair[\s-]+accessible|accessible|barrier[\s-]+free)\b",
        lower,
    ):
        needs.append("accessible")
    return list(dict.fromkeys(needs))


def _parse_purpose(text):
    lower = text.lower()
    purposes = (
        (r"\b(?:guest\s+lecture|industry\s+speaker|speaker|talk|lecture|seminar(?!\s+hall))\b", "guest_lecture"),
        (r"\b(?:institute[- ]wide|campus[- ]wide|institute\s+event|college\s+event)\b", "institute_event"),
        (r"\b(?:department|dept|farewell|poster\s+session)\b", "department_event"),
        (r"\b(?:club|coding\s+contest|hackathon|quiz|contest|competition|society|"
         r"stage\s+play|student\s+event|meetup)\b", "club_event"),
    )
    for pattern, category in purposes:
        if re.search(pattern, lower):
            return category
    return None


def _parse_groups(data, text):
    lower = text.lower()
    if not re.search(
        r"\b(?:compulsory|mandatory|required|must\s+attend|attendance\s+is\s+required)\b",
        lower,
    ):
        return []
    matched = []
    ordinals = {1: "first", 2: "second", 3: "third", 4: "fourth"}
    for group in data["student_groups"]:
        ordinal = ordinals.get(group["year"])
        if ordinal is None:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(group['id'].lower())}(?![a-z0-9])", lower):
            matched.append(group["id"])
            continue
        department_name = {
            "ME": "mechanical",
            "CSE": "computer science",
            "ECE": "electronics",
            "EE": "electrical",
            "CE": "civil",
        }.get(group["department"])
        if (
            department_name
            and department_name in lower
            and re.search(
                rf"\b(?:year\s+{group['year']}|{ordinal}\s+years?)\b",
                lower,
            )
        ):
            matched.append(group["id"])
    return matched


def _parse_audience(text):
    if re.search(
        r"\b(?:students?|learners|vidyarthi|vidyarthiyon)\b",
        text,
        re.IGNORECASE,
    ):
        return "students"
    return None


def _requested_room_constraint(data, room_id, needs, attendance, language):
    if not room_id:
        return None

    room = next(
        (candidate for candidate in data["rooms"] if candidate["id"] == room_id),
        None,
    )
    if room is None:
        return None

    features = set(room.get("features", []))
    if room.get("accessible"):
        features.add("accessible")
    unmet_features = sorted(set(needs) - features)
    if attendance is not None and room["capacity"] < attendance:
        unmet_features.append("capacity")
    if "event" not in room.get("allowed_uses", []):
        unmet_features.append("event use")
    if not unmet_features:
        return None

    alternatives = []
    for candidate in data["rooms"]:
        if (
            candidate["id"] == room_id
            or candidate["type"] != room["type"]
            or "event" not in candidate.get("allowed_uses", [])
            or (attendance is not None and candidate["capacity"] < attendance)
        ):
            continue
        candidate_features = set(candidate.get("features", []))
        if candidate.get("accessible"):
            candidate_features.add("accessible")
        if set(needs).issubset(candidate_features):
            alternatives.append({
                "room_id": candidate["id"],
                "room_name": candidate["name"],
            })

    if language == "hinglish":
        message = (
            f"{room['name']} ({room_id}) requested room requirements meet nahi karta "
            f"({', '.join(unmet_features)})."
        )
        if alternatives:
            message += " Room-fit alternatives: " + ", ".join(
                item["room_id"] for item in alternatives
            ) + "."
    else:
        message = (
            f"{room['name']} ({room_id}) does not meet the requested "
            f"requirements ({', '.join(unmet_features)})."
        )
        if alternatives:
            message += " Room-fit alternatives: " + ", ".join(
                item["room_id"] for item in alternatives
            ) + "."

    return {
        "requested_room_id": room_id,
        "unmet_requirements": unmet_features,
        "suggested_rooms": alternatives,
        "message": message,
    }


def _period_covering_time(data, minutes):
    for period in data["calendar"]["periods"]:
        hour, minute = map(int, period["start"].split(":"))
        if hour * 60 + minute == minutes:
            return period["id"]
    return None


def _period_range(data, start_minute, end_minute):
    periods = data["calendar"]["periods"]
    start_id = _period_covering_time(data, start_minute)
    end_id = next(
        (
            period["id"]
            for period in periods
            if tuple(map(int, period["end"].split(":"))) == (
                end_minute // 60,
                end_minute % 60,
            )
        ),
        None,
    )
    if start_id is None or end_id is None or end_id < start_id:
        return None
    return start_id, end_id


def _time_question(language, error=None):
    if language == "hinglish":
        if error == "meridiem":
            return "Time AM/PM ke saath batayein (jaise 6 AM ya 6 PM)."
        return "Exact start aur end time batayein, schedule ke period times ke mutabik."
    if error == "meridiem":
        return "Please specify AM or PM (for example, 6 AM or 6 PM)."
    return "Please give an exact start and end time that matches the schedule periods."


def _follow_up_questions(language, missing):
    english = {
        "date": "Which date or weekday should I check?",
        "date_year": "Which year is that date in?",
        "future_date": "That date is in the past. Which future date should I check?",
        "room_type": "What room type do you need (for example, classroom, seminar hall, or computer lab)?",
        "capacity": "How many people should the room accommodate?",
        "duration": "How long should the booking be?",
        "time": _time_question("english"),
        "time_meridiem": _time_question("english", "meridiem"),
        "purpose": "What is the event or booking purpose?",
        "room_type_unsupported": "The dataset has no rooms for that day/type. Which weekday or room type should I check?",
        "past_date": "That date is in the past. Please give a future date for a booking check.",
        "invalid_time": _time_question("english"),
        "invalid_duration": "Duration must be a whole number of one-hour schedule periods.",
    }
    hinglish = {
        "date": "Kaunsi date ya weekday check karna hai?",
        "date_year": "Is date ka year kya hai?",
        "future_date": "Yeh date beet chuki hai. Kaunsi future date check karun?",
        "room_type": "Kaunsa room chahiye—classroom, seminar hall, ya computer lab?",
        "capacity": "Room mein kitne log aayenge?",
        "duration": "Booking kitne ghante ke liye chahiye?",
        "time": _time_question("hinglish"),
        "time_meridiem": _time_question("hinglish", "meridiem"),
        "purpose": "Booking ka purpose kya hai?",
        "room_type_unsupported": "Is din/type ke rooms dataset mein nahi hain. Kaunsa weekday ya room type check karun?",
        "past_date": "Yeh date beet chuki hai. Booking check ke liye future date batayein.",
        "invalid_time": _time_question("hinglish"),
        "invalid_duration": "Duration poore one-hour schedule periods mein honi chahiye.",
    }
    table = hinglish if language == "hinglish" else english
    return [table[item] for item in dict.fromkeys(missing)]


def parse_natural_language_booking(text, data, today=None):
    normalized = re.sub(r"\s+", " ", text.strip())
    lower = normalized.lower()
    language = detect_language(normalized)
    today = today or (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()

    availability_query = bool(re.search(
        r"\b(?:kitne\s+baje|what\s+time|when\s+(?:is|will|can))\b|"
        r"\b(?:is|are)\s+(?:the\s+|an?\s+)?(?:[\w-]+\s+){0,3}"
        r"(?:khali|khaali|free|available)\b|"
        r"\b(?:khali|khaali|free|available)\s+"
        r"(?:hai|hoga|hogi|is|at|on|tomorrow|today|kal|parso)\b|"
        r"\b(?:khali|khaali|free|available)\s*\?$",
        lower,
    ))
    date_info = _parse_date(lower, today)
    room_types, requested_room = _parse_room(data, lower)
    attendance = _parse_attendance(lower)
    duration, duration_error = _parse_duration(lower)
    time_info = _parse_time(lower)
    needs = _parse_needs(lower)
    purpose = _parse_purpose(lower)
    audience = _parse_audience(normalized)
    room_constraint = _requested_room_constraint(
        data,
        requested_room,
        needs,
        attendance,
        language,
    )
    groups = _parse_groups(data, normalized)
    missing = []

    if date_info["error"] in ("date_year_missing", "date_day_missing"):
        missing.append("date_year")
    elif date_info["error"] == "invalid_date":
        missing.append("date")
    elif date_info["day"] is None:
        missing.append("date")
    if room_types == []:
        missing.append("room_type")

    if not availability_query:
        if attendance is None:
            missing.append("capacity")
        if duration is None and time_info["range"] is None:
            missing.append("duration")
        if date_info["error"] == "date_missing" and "date" not in missing:
            missing.append("date")
        if duration_error:
            missing.append("invalid_duration")

    if time_info["error"] == "meridiem":
        missing.append("time_meridiem")
    elif time_info["error"] in ("invalid_range", "invalid_time"):
        missing.append("invalid_time")

    windows = []
    selected_days = date_info.get("days") or (
        [date_info["day"]] if date_info["day"] else []
    )
    if time_info["range"] is not None:
        covered = _period_range(data, *time_info["range"])
        if covered is None:
            missing.append("invalid_time")
        else:
            windows = [
                {
                    "day": day,
                    "from_period": covered[0],
                    "to_period": covered[1],
                }
                for day in selected_days
            ]
            if duration is None:
                duration = covered[1] - covered[0] + 1
            elif duration != covered[1] - covered[0] + 1:
                missing.append("invalid_duration")
    elif time_info["start"] is not None:
        start_period = _period_covering_time(data, time_info["start"])
        if start_period is None:
            missing.append("invalid_time")
        elif duration:
            end_period = start_period + duration - 1
            valid_periods = {
                period["id"]
                for period in data["calendar"]["periods"]
            }
            if all(period in valid_periods for period in range(start_period, end_period + 1)):
                windows = [
                    {
                        "day": day,
                        "from_period": start_period,
                        "to_period": end_period,
                    }
                    for day in selected_days
                ]
            else:
                missing.append("time")
        elif not availability_query:
            missing.append("duration")
    elif not availability_query:
        missing.append("time")

    if any(day not in data["calendar"]["days"] for day in selected_days):
        missing.append("room_type_unsupported")
    if date_info["date"] and date_info["date"] < today and not _past_context(lower):
        missing.append("past_date")

    if date_info["day"] and windows:
        if not date_info.get("days"):
            for window in windows:
                window["day"] = date_info["day"]

    if not availability_query and not windows and date_info["day"] and duration:
        if not time_info["match"]:
            missing.append("time")

    if availability_query and not (time_info["range"] or time_info["start"]):
        windows = [
            {
                "day": day,
                "from_period": period["id"],
                "to_period": period["id"],
            }
            for day in selected_days
            for period in data["calendar"]["periods"]
        ]

    if missing:
        follow_ups = _follow_up_questions(language, missing)
        answer = " ".join(follow_ups)
        return {
            "status": "needs_clarification",
            "language": language,
            "intent": "availability_query" if availability_query else "booking_preview",
            "message": answer,
            "follow_up_questions": follow_ups,
            "room_constraint": room_constraint,
            "structured_request": {
                "attendance": attendance,
                "duration_periods": duration,
                "windows": windows,
                "needs": needs,
                "room_types": room_types or DEFAULT_EVENT_ROOM_TYPES,
                "mandatory_for_groups": groups,
                "requested_room": requested_room,
                "purpose": purpose,
                "audience": audience,
                "date": date_info["date"].isoformat() if date_info["date"] else None,
                "weekdays": selected_days,
                "follow_up": answer,
            },
            "availability": None,
            "approval_status": "not_approved",
        }

    request = {
        "attendance": attendance if attendance is not None else 1,
        "duration_periods": duration or 1,
        "windows": windows,
        "needs": needs,
        "room_types": room_types or DEFAULT_EVENT_ROOM_TYPES,
        "mandatory_for_groups": groups,
        "requested_room": requested_room,
        "purpose": purpose,
        "audience": audience,
        "requested_category": purpose,
        "date": date_info["date"].isoformat() if date_info["date"] else None,
        "weekday": date_info["day"],
        "weekdays": selected_days,
        "follow_up": None,
        "availability_query": availability_query,
    }
    return {
        "status": "ready_for_availability_check",
        "language": language,
        "intent": "availability_query" if availability_query else "booking_preview",
        "message": "",
        "follow_up_questions": [],
        "room_constraint": room_constraint,
        "structured_request": request,
        "availability": None,
        "approval_status": "not_approved",
    }


def local_date_today():
    return (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()
