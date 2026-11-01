
def add_soft_objectives(model, sessions, assignments, faculty_by_id=None):
    """
    Optimize official soft penalties S2, S3, S4 and S5.

    S2: Minimize student group timetable gaps.
    S3: Penalize daily teaching hours above six.
    S4: Penalize repeated lectures of the same course.
    S5: Penalize room utilization below 40%.

    Elective baskets count once for group occupancy and S3.
    """

    group_day_periods = {}
    group_day_hours = {}

    ordinary_lectures = {}
    basket_lectures = {}

    basket_representatives = {}

    # Find one representative per elective basket.
    for session in sessions:

        basket_id = session.get("basket_slot")

        if basket_id:

            basket_representatives.setdefault(
                basket_id,
                session["id"]
            )

    # Build objective expressions.
    for session in sessions:

        session_id = session["id"]
        basket_id = session.get("basket_slot")
        duration = session["duration"]

        count_for_groups = (
            basket_id is None
            or basket_representatives[basket_id] == session_id
        )

        for candidate in assignments[session_id]["candidates"]:

            day = candidate["day"]
            start = candidate["start_period"]
            selected = candidate["selected"]

            # -----------------------------------------
            # S2 and S3: Student group occupancy
            # -----------------------------------------

            if count_for_groups:

                for group_id in session["groups"]:

                    key = (group_id, day)

                    group_day_periods.setdefault(
                        key, {}
                    )

                    group_day_hours.setdefault(
                        key, []
                    )

                    group_day_hours[key].append(
                        duration * selected
                    )

                    for period in range(
                        start,
                        start + duration
                    ):

                        group_day_periods[key].setdefault(
                            period, []
                        ).append(selected)

            # -----------------------------------------
            # S4: Ordinary lectures
            # -----------------------------------------

            if (
                session["kind"] == "lecture"
                and basket_id is None
            ):

                for group_id in session["groups"]:

                    key = (
                        group_id,
                        session["course_id"],
                        day
                    )

                    ordinary_lectures.setdefault(
                        key, []
                    ).append(selected)

            # -----------------------------------------
            # S4: Elective basket lectures
            # -----------------------------------------

            if basket_id:

                key = (
                    session["course_id"],
                    day
                )

                basket_lectures.setdefault(
                    key, []
                ).append(selected)

    penalty_terms = []

    # Faculty preferences are soft rather than availability constraints.
    if faculty_by_id:
        for session in sessions:
            for candidate in assignments[session["id"]]["candidates"]:
                if candidate.get("faculty_preference_avoided"):
                    penalty_terms.append(candidate["selected"])

    # =================================================
    # S2: Student group timetable gaps
    # =================================================

    s2_index = 0

    for (group_id, day), period_terms in (
        group_day_periods.items()
    ):

        teaching_periods = list(range(1, 10))

        occupied = {}

        for period in teaching_periods:

            terms = period_terms.get(period, [])

            occupied_var = model.NewBoolVar(
                f"s2_occupied_{s2_index}_{period}"
            )

            if terms:

                model.AddMaxEquality(
                    occupied_var,
                    terms
                )

            else:

                model.Add(
                    occupied_var == 0
                )

            occupied[period] = occupied_var

        first = model.NewIntVar(
            1,
            9,
            f"s2_first_{s2_index}"
        )

        last = model.NewIntVar(
            1,
            9,
            f"s2_last_{s2_index}"
        )

        # Exact first occupied period.
        for period in teaching_periods:

            model.Add(
                first <= period
            ).OnlyEnforceIf(
                occupied[period]
            )

            if period > 1:

                model.Add(
                    first >= period
                ).OnlyEnforceIf([
                    occupied[period],
                    *[
                        occupied[p].Not()
                        for p in range(1, period)
                    ]
                ])

        # Exact last occupied period.
        for period in teaching_periods:

            model.Add(
                last >= period
            ).OnlyEnforceIf(
                occupied[period]
            )

            if period < 9:

                model.Add(
                    last <= period
                ).OnlyEnforceIf([
                    occupied[period],
                    *[
                        occupied[p].Not()
                        for p in range(period + 1, 10)
                    ]
                ])

        # Count unoccupied periods between first and last.
        for period in teaching_periods:

            if period == 1 or period == 9:
                continue

            gap = model.NewBoolVar(
                f"s2_gap_{s2_index}_{period}"
            )

            before = model.NewBoolVar(
                f"s2_before_{s2_index}_{period}"
            )

            after = model.NewBoolVar(
                f"s2_after_{s2_index}_{period}"
            )

            model.Add(
                first <= period
            ).OnlyEnforceIf(before)

            model.Add(
                first >= period + 1
            ).OnlyEnforceIf(before.Not())

            model.Add(
                last >= period
            ).OnlyEnforceIf(after)

            model.Add(
                last <= period - 1
            ).OnlyEnforceIf(after.Not())

            model.AddBoolAnd([
                occupied[period].Not(),
                before,
                after
            ]).OnlyEnforceIf(gap)

            model.AddBoolOr([
                occupied[period],
                before.Not(),
                after.Not()
            ]).OnlyEnforceIf(gap.Not())

            penalty_terms.append(gap)

        s2_index += 1

    # =================================================
    # S3: Daily teaching hours above six
    # =================================================

    for index, (key, terms) in enumerate(
        group_day_hours.items()
    ):

        total_hours = sum(terms)

        excess = model.NewIntVar(
            0,
            100,
            f"s3_excess_{index}"
        )

        model.AddMaxEquality(
            excess,
            [
                total_hours - 6,
                0
            ]
        )

        penalty_terms.append(
            3 * excess
        )

    # =================================================
    # S4: Ordinary repeated lectures
    # =================================================

    for index, (key, terms) in enumerate(
        ordinary_lectures.items()
    ):

        lecture_count = sum(terms)

        excess = model.NewIntVar(
            0,
            max(0, len(terms) - 1),
            f"s4_ordinary_excess_{index}"
        )

        model.AddMaxEquality(
            excess,
            [
                lecture_count - 1,
                0
            ]
        )

        penalty_terms.append(
            3 * excess
        )

    # =================================================
    # S4: Elective basket repeated lectures
    # =================================================

    for index, (key, terms) in enumerate(
        basket_lectures.items()
    ):

        lecture_count = sum(terms)

        excess = model.NewIntVar(
            0,
            max(0, len(terms) - 1),
            f"s4_basket_excess_{index}"
        )

        model.AddMaxEquality(
            excess,
            [
                lecture_count - 1,
                0
            ]
        )

        penalty_terms.append(
            3 * excess
        )

    # =================================================
    # S5: Room utilization below 40%
    # =================================================

    s5_terms = []

    for session in sessions:

        session_size = session["size"]

        for candidate in assignments[session["id"]]["candidates"]:

            room_capacity = candidate["room_capacity"]

            # Exact validator condition:
            # session_size / room_capacity < 0.4

            if session_size * 5 < room_capacity * 2:

                s5_terms.append(
                    candidate["selected"]
                )

    penalty_terms.extend(s5_terms)

    # Minimize all modeled soft penalties.
    model.Minimize(
        sum(penalty_terms)
    )

    print("Soft objectives added successfully.")
    print("Optimizing S2: student group timetable gaps.")
    print("Optimizing S3: daily teaching hours.")
    print("Optimizing S4: repeated daily course lectures.")
    print("Optimizing S5: room utilization.")
