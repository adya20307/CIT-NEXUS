export function filterExams(exams, { day, session, search }) {
  const query = search.trim().toLowerCase();

  return exams.filter(exam => {
    const matchesDay = day === "ALL" || exam.day === day;
    const matchesSession = session === "ALL" || exam.session === session;
    const searchableFields = [
      exam.exam_id,
      ...exam.course_ids,
      ...exam.course_names,
      ...exam.groups.flatMap(group => [group.id, group.name]),
    ];
    const matchesSearch = searchableFields
      .join(" ")
      .toLowerCase()
      .includes(query);

    return matchesDay && matchesSession && matchesSearch;
  });
}
