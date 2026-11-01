
def add_hard_constraints(model, sessions, assignments, faculty_by_id):
    faculty_intervals = {}
    group_intervals = {}
    room_intervals = {}
    faculty_daily_hours = {}
    basket_sessions = {}

    # One representative session per elective basket.
    # All sessions in a basket share the same student groups and time.
    basket_representatives = {}

    for session in sessions:
        basket_id = session.get("basket_slot")
        if basket_id:
            basket_sessions.setdefault(basket_id, []).append(session)
            basket_representatives.setdefault(basket_id, session["id"])

    for session in sessions:
        session_id = session["id"]
        assignment = assignments[session_id]

        if not assignment["choices"]:
            raise ValueError(
                f"No valid candidates for session: {session_id}"
            )

        model.AddExactlyOne(assignment["choices"])

        faculty_id = session["faculty_id"]
        max_hours = faculty_by_id[faculty_id]["max_hours_per_day"]

        basket_id = session.get("basket_slot")

        for candidate in assignment["candidates"]:
            selected = candidate["selected"]
            interval = candidate["interval"]
            day = candidate["day"]
            room_id = candidate["room_id"]

            faculty_key = (day, faculty_id)
            room_key = (day, room_id)

            faculty_intervals.setdefault(
                faculty_key, []
            ).append(interval)

            room_intervals.setdefault(
                room_key, []
            ).append(interval)

            # Count faculty teaching hours.
            faculty_daily_hours.setdefault(
                faculty_key, []
            ).append(session["duration"] * selected)

            # For an elective basket, use only one representative
            # interval for student-group conflict checking.
            # All sessions in the basket are synchronized below.
            include_group_interval = (
                basket_id is None
                or basket_representatives[basket_id] == session_id
            )

            if include_group_interval:
                for group_id in session["groups"]:
                    group_key = (day, group_id)

                    group_intervals.setdefault(
                        group_key, []
                    ).append(interval)

    # Faculty cannot have overlapping sessions.
    for intervals in faculty_intervals.values():
        if len(intervals) > 1:
            model.AddNoOverlap(intervals)

    # Student groups cannot have overlapping ordinary classes.
    # Each elective basket contributes one representative interval.
    for intervals in group_intervals.values():
        if len(intervals) > 1:
            model.AddNoOverlap(intervals)

    # Rooms cannot host overlapping sessions.
    for intervals in room_intervals.values():
        if len(intervals) > 1:
            model.AddNoOverlap(intervals)

    # Faculty maximum teaching hours per day.
    for (day, faculty_id), hour_terms in faculty_daily_hours.items():
        limit = faculty_by_id[faculty_id]["max_hours_per_day"]
        model.Add(sum(hour_terms) <= limit)

    # Synchronize sessions belonging to the same elective basket.
    for basket_id, basket in basket_sessions.items():
        first_session = basket[0]

        common_slots = {
            (c["day"], c["start_period"])
            for c in assignments[first_session["id"]]["candidates"]
        }

        for session in basket[1:]:
            session_slots = {
                (c["day"], c["start_period"])
                for c in assignments[session["id"]]["candidates"]
            }
            common_slots.intersection_update(session_slots)

        if not common_slots:
            raise ValueError(
                f"No common timetable slot for basket: {basket_id}"
            )

        slot_variables = {}

        for day, start_period in sorted(common_slots):
            slot_var = model.NewBoolVar(
                f"basket_{basket_id}_{day}_{start_period}"
            )
            slot_variables[(day, start_period)] = slot_var

            for session in basket:
                matching_choices = [
                    c["selected"]
                    for c in assignments[session["id"]]["candidates"]
                    if (
                        c["day"] == day
                        and c["start_period"] == start_period
                    )
                ]

                model.Add(sum(matching_choices) == slot_var)

        model.AddExactlyOne(list(slot_variables.values()))
