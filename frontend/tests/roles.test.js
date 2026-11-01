import assert from "node:assert/strict";
import test from "node:test";
import { getDashboardPath, getDashboardRole } from "../src/auth/roles.js";

test("maps each verified application role to its dashboard", () => {
  for (const [role, path] of [
    ["STUDENT", "/student"],
    ["FACULTY", "/faculty"],
    ["ADMIN", "/admin"],
  ]) {
    assert.equal(getDashboardRole([role]), role);
    assert.equal(getDashboardPath([role]), path);
  }
});

test("denies missing or unrecognized application roles", () => {
  for (const roles of [undefined, [], ["department_coordinator"]]) {
    assert.equal(getDashboardRole(roles), null);
    assert.equal(getDashboardPath(roles), null);
  }
});

test("uses the highest dashboard role when multiple roles are assigned", () => {
  assert.equal(getDashboardPath(["STUDENT", "FACULTY", "ADMIN"]), "/admin");
});
