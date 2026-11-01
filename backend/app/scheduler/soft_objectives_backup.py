
def add_soft_objectives(model, sessions, assignments):
    """
    Optimize official timetable soft penalties S2, S3 and S4.

    S2: Minimize gaps in student group timetables.
    S3: Penalize daily teaching hours above 6.
    S4: Penalize repeated lectures of the same course per day.

    Elective baskets are counted once for student group
    occupancy and daily teaching hours.
    """

    group_day_periods = {}
    group_day_hours = {}

    ordinary_lectures = {}
    basket_lectures = {}

    basket_representatives = {}

    # Identify one representative per elective basket.
    for session in sessions:
        basket_id = session.get("basket_slot")

        if basket_id:
            basket_representatives.setdefault(
                basket_id,
                session["id"]
            )

    # -------------------------------------------------
    # Build occupancy and lecture-count expressions.
    # -------------------------------------------------

    for session in sessions:

        session_id = session["id"]
        basket_id = session.get("basket_slot")
        duration = session["duration"]

        # Count an elective basket only once for groups.
        count_for_groups = (
            basket_id is None
            or basket_representatives[basket_id] == session_id
        )

        for candidate in assignments[session_id]["candidates"]:

            day = candidate["day"]
            start = candidate["start_period"]
            selected = candidate["selected"]

            occupied_periods = range(
                start,
                start + duration
            )

            # -----------------------------------------
            # S2: Group period occupancy
            # -----------------------------------------

            if count_for_groups:

                for group_id in session["groups"]:

                    key = (group_id, day)

                    group_day_periods.setdefault(
                        key, {}
                    )

                    for period in occupied_periods:

                        group_day_periods[key].setdefault(
                            period, []
                        ).append(selected)

            # -----------------------------------------
            # S3: Daily teaching hours
            # -----------------------------------------

            if count_for_groups:

                for group_id in session["groups"]:

                    key = (group_id, day)

                    group_day_hours.setdefault(
                        key, []
                    ).append(duration * selected)

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

    # =================================================
    # S2: Minimize group timetable gaps
    # =================================================

    s2_index = 0

    for (group_id, day), period_variables in (
        group_day_periods.items()
    ):

        periods = sorted(period_variables)

        if len(periods) <= 1:
            continue

        occupied = {}

        for period in periods:

            # A period is occupied if at least one
            # selected session uses that period.
            occupied_var = model.NewBoolVar(
                f"s2_occupied_{s2_index}_{period}"
            )

            model.AddMaxEquality(
                occupied_var,
                period_variables[period]
            )

            occupied[period] = occupied_var

        # Find the first and last occupied periods.
        first = model.NewIntVar(
            min(periods),
            max(periods),
            f"s2_first_{s2_index}"
        )

        last = model.NewIntVar(
            min(periods),
            max(periods),
            f"s2_last_{s2_index}"
        )

        for period in periods:

            model.Add(
                first <= period
            ).OnlyEnforceIf(occupied[period])

            model.Add(
                last >= period
            ).OnlyEnforceIf(occupied[period])

        # At least one occupied period exists every day
        # represented in this dictionary.
        model.Add(
            first == min(periods)
        ).OnlyEnforceIf(occupied[min(periods)])

        model.Add(
            last == max(periods)
        ).OnlyEnforceIf(occupied[max(periods)])

        # A gap exists when a period between the first
        # and last occupied periods is not occupied.
        gap_variables = []

        for period in periods:

            if period == min(periods) or period == max(periods):
                continue

            gap = model.NewBoolVar(
                f"s2_gap_{s2_index}_{period}"
            )

            model.Add(
                gap <= occupied[period].Not()
            )

            model.Add(
                gap <= first <= period
            ) if False else None

            # If first <= period and last >= period,
            # an unoccupied period is a gap.
            before_or_at_first = model.NewBoolVar(
                f"s2_before_first_{s2_index}_{period}"
            )

            after_or_at_last = model.NewBoolVar(
                f"s2_after_last_{s2_index}_{period}"
            )

            model.Add(
                first <= period
            ).OnlyEnforceIf(before_or_at_first)

            model.Add(
                first > period
            ).OnlyEnforceIf(before_or_at_first.Not())

            model.Add(
                last >= period
            ).OnlyEnforceIf(after_or_at_last)

            model.Add(
                last < period
            ).OnlyEnforceIf(after_or_at_last.Not())

            model.AddBoolAnd([
                occupied[period].Not(),
                before_or_at_first,
                after_or_at_last
            ]).OnlyEnforceIf(gap)

            model.AddBoolOr([
                occupied[period],
                before_or_at_first.Not(),
                after_or_at_last.Not()
            ]).OnlyEnforceIf(gap.Not())

            gap_variables.append(gap)

        penalty_terms.extend(gap_variables)
        s2_index += 1

    # =================================================
    # S3: Daily teaching hours above six
    # =================================================

    for index, (key, terms) in enumerate(
        group_day_hours.items()
    ):

        total_hours = sum(terms)

        excess_hours = model.NewIntVar(
            0,
            100,
            f"s3_excess_{index}"
        )

        model.AddMaxEquality(
            excess_hours,
            [total_hours - 6, 0]
        )

        penalty_terms.append(
            3 * excess_hours
        )

    # =================================================
    # S4: Ordinary repeated lectures
    # =================================================

    for index, (key, terms) in enumerate(
        ordinary_lectures.items()
    ):

        lecture_count = sum(terms)

        repeated_lectures = model.NewIntVar(
            0,
            max(0, len(terms) - 1),
            f"s4_ordinary_excess_{index}"
        )

        model.AddMaxEquality(
            repeated_lectures,
            [lecture_count - 1, 0]
        )

        penalty_terms.append(
            3 * repeated_lectures
        )

    # =================================================
    # S4: Elective basket repeated lectures
    # =================================================

    for index, (key, terms) in enumerate(
        basket_lectures.items()
    ):

        lecture_count = sum(terms)

        repeated_lectures = model.NewIntVar(
            0,
            max(0, len(terms) - 1),
            f"s4_basket_excess_{index}"
        )

        model.AddMaxEquality(
            repeated_lectures,
            [lecture_count - 1, 0]
        )

        penalty_terms.append(
            3 * repeated_lectures
        )

    # Minimize all modeled soft penalties.
    model.Minimize(sum(penalty_terms))

    print("Soft objectives added successfully.")
    print("Optimizing S2: student group timetable gaps.")
    print("Optimizing S3: daily group teaching hours.")
    print("Optimizing S4: repeated daily course lectures.")
