export function getEntitySessions(sessions, kind, id) {
  if (kind === "group") {
    return sessions.filter(session => session.group_ids.includes(id));
  }
  if (kind === "faculty") {
    return sessions.filter(session => session.faculty_id === id);
  }
  if (kind === "room") {
    return sessions.filter(session => session.room_id === id);
  }
  return [];
}

export function buildOccupancyMap(sessions) {
  const occupancy = new Map();
  for (const session of sessions) {
    for (let period = session.start_period; period <= session.end_period; period += 1) {
      const key = `${session.day}:${period}`;
      const entries = occupancy.get(key) || [];
      entries.push(session);
      occupancy.set(key, entries);
    }
  }
  return occupancy;
}

export function buildBookingMap(bookings) {
  const occupancy = new Map();
  for (const booking of bookings) {
    if (booking.status !== "approved") continue;
    for (
      let period = booking.start_period;
      period <= booking.end_period;
      period += 1
    ) {
      const key = `${booking.day}:${period}`;
      const entries = occupancy.get(key) || [];
      entries.push(booking);
      occupancy.set(key, entries);
    }
  }
  return occupancy;
}

export function buildUnavailabilityMap(unavailability, day) {
  const periods = new Map();
  for (const item of unavailability || []) {
    if (item.day !== day) continue;
    for (const period of item.periods) {
      const reasons = periods.get(period) || [];
      reasons.push(item.reason);
      periods.set(period, reasons);
    }
  }
  return periods;
}

export function countFreePeriods(days, periods, occupied, unavailable = new Map()) {
  let free = 0;
  for (const day of days) {
    const unavailablePeriods = unavailable.get(day) || new Set();
    for (const period of periods) {
      if (!occupied.has(`${day}:${period}`) && !unavailablePeriods.has(period)) {
        free += 1;
      }
    }
  }
  return free;
}

export function getDailyWorkload(sessions, days) {
  return Object.fromEntries(days.map(day => [
    day,
    sessions
      .filter(session => session.day === day)
      .reduce((hours, session) => hours + session.duration, 0),
  ]));
}

export function getApprovedBookings(bookings) {
  return bookings.filter(booking => booking.status === "approved");
}

export function getSessionSearchText(session) {
  return [
    session.session_id,
    session.course_id,
    session.course_title,
    session.faculty_name,
    session.room_id,
    ...session.groups.map(group => group.name),
  ].join(" ").toLowerCase();
}
