const DASHBOARD_ROLES = ["ADMIN", "FACULTY", "STUDENT"];

const DASHBOARD_PATHS = {
  ADMIN: "/admin",
  FACULTY: "/faculty",
  STUDENT: "/student",
};

export function getDashboardRole(roles) {
  if (!Array.isArray(roles)) return null;
  return DASHBOARD_ROLES.find(role => roles.includes(role)) || null;
}

export function getDashboardPath(roles) {
  const role = getDashboardRole(roles);
  return role ? DASHBOARD_PATHS[role] : null;
}
