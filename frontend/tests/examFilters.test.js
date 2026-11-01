import assert from "node:assert/strict";
import test from "node:test";
import { filterExams } from "../src/examFilters.js";

const exams = [
  {
    exam_id: "EXAM-01",
    day: "MON",
    session: "AM",
    course_ids: ["CS101"],
    course_names: ["Introduction to Computing"],
    groups: [{ id: "Y1-A", name: "Year 1 Group A" }],
  },
  {
    exam_id: "EXAM-02",
    day: "TUE",
    session: "PM",
    course_ids: ["MA202"],
    course_names: ["Discrete Mathematics"],
    groups: [{ id: "Y2-B", name: "Year 2 Group B" }],
  },
];

const filters = overrides => ({
  day: "ALL",
  session: "ALL",
  search: "",
  ...overrides,
});

test("filters by exam day", () => {
  assert.deepEqual(
    filterExams(exams, filters({ day: "MON" })).map(exam => exam.exam_id),
    ["EXAM-01"],
  );
});

test("filters by AM and PM session", () => {
  assert.deepEqual(
    filterExams(exams, filters({ session: "AM" })).map(exam => exam.exam_id),
    ["EXAM-01"],
  );
  assert.deepEqual(
    filterExams(exams, filters({ session: "PM" })).map(exam => exam.exam_id),
    ["EXAM-02"],
  );
});

test("searches exam ID, course ID/title, and student group ID/name", () => {
  for (const search of [
    "exam-02",
    "MA202",
    "discrete mathematics",
    "Y2-B",
    "Year 2 Group B",
  ]) {
    assert.deepEqual(
      filterExams(exams, filters({ search })).map(exam => exam.exam_id),
      ["EXAM-02"],
      `Expected search '${search}' to match EXAM-02`,
    );
  }
});

test("combines day, session, and search filters", () => {
  assert.deepEqual(
    filterExams(
      exams,
      filters({ day: "MON", session: "AM", search: "CS101" }),
    ).map(exam => exam.exam_id),
    ["EXAM-01"],
  );
  assert.deepEqual(
    filterExams(
      exams,
      filters({ day: "MON", session: "PM", search: "CS101" }),
    ),
    [],
  );
});
