import test from "node:test";
import assert from "node:assert/strict";
import {
  buildBookingMap,
  buildOccupancyMap,
  buildUnavailabilityMap,
  countFreePeriods,
  getApprovedBookings,
  getDailyWorkload,
  getEntitySessions,
} from "../src/timetableUtils.js";

const sessions = [
  {
    session_id: "CS191-A-P1",
    group_ids: ["Y1-A"],
    faculty_id: "F03",
    room_id: "CS-LAB-1",
    day: "MON",
    start_period: 2,
    end_period: 4,
    duration: 3,
  },
  {
    session_id: "MA101-AB-L1",
    group_ids: ["Y1-A", "Y1-B"],
    faculty_id: "F19",
    room_id: "LH-1",
    day: "TUE",
    start_period: 1,
    end_period: 1,
    duration: 1,
  },
];

test("entity selection includes shared-group sessions and filters faculty and rooms", () => {
  assert.deepEqual(
    getEntitySessions(sessions, "group", "Y1-A").map(session => session.session_id),
    ["CS191-A-P1", "MA101-AB-L1"],
  );
  assert.deepEqual(
    getEntitySessions(sessions, "group", "Y1-B").map(session => session.session_id),
    ["MA101-AB-L1"],
  );
  assert.deepEqual(
    getEntitySessions(sessions, "faculty", "F03").map(session => session.session_id),
    ["CS191-A-P1"],
  );
  assert.deepEqual(
    getEntitySessions(sessions, "room", "LH-1").map(session => session.session_id),
    ["MA101-AB-L1"],
  );
});

test("multi-period sessions occupy each teaching period", () => {
  const occupancy = buildOccupancyMap(sessions);
  assert.equal(occupancy.get("MON:2")[0].session_id, "CS191-A-P1");
  assert.equal(occupancy.get("MON:3")[0].session_id, "CS191-A-P1");
  assert.equal(occupancy.get("MON:4")[0].session_id, "CS191-A-P1");
  assert.equal(occupancy.has("MON:5"), false);
});

test("free periods exclude faculty unavailability rather than counting it as free", () => {
  const occupancy = buildOccupancyMap(sessions);
  const unavailability = new Map([
    ["MON", new Set([1])],
    ["TUE", new Set()],
  ]);
  assert.equal(
    countFreePeriods(["MON", "TUE"], [1, 2, 3, 4], occupancy, unavailability),
    3,
  );
  const mapped = buildUnavailabilityMap(
    [{ day: "MON", periods: [1, 2], reason: "Meeting" }],
    "MON",
  );
  assert.deepEqual(mapped.get(1), ["Meeting"]);
});

test("daily workload sums the durations of scheduled sessions", () => {
  assert.deepEqual(
    getDailyWorkload(sessions, ["MON", "TUE", "WED"]),
    { MON: 3, TUE: 1, WED: 0 },
  );
});

test("room calendars only occupy periods for approved bookings", () => {
  const bookings = [
    { request_id: "BR-1", status: "approved", day: "THU", start_period: 6, end_period: 7 },
    { request_id: "BR-2", status: "alternative_offered", day: "THU", start_period: 1, end_period: 2 },
    { request_id: "BR-3", status: "rejected", day: "THU", start_period: 3, end_period: 3 },
  ];
  assert.deepEqual(getApprovedBookings(bookings).map(booking => booking.request_id), ["BR-1"]);
  const occupancy = buildBookingMap(bookings);
  assert.equal(occupancy.get("THU:6")[0].request_id, "BR-1");
  assert.equal(occupancy.get("THU:7")[0].request_id, "BR-1");
  assert.equal(occupancy.has("THU:1"), false);
  assert.equal(occupancy.has("THU:3"), false);
});
