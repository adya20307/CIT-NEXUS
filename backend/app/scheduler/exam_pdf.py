import textwrap


PAGE_WIDTH = 842
PAGE_HEIGHT = 595
LEFT_MARGIN = 36
TOP_MARGIN = 555
LINE_HEIGHT = 13
LINES_PER_PAGE = 39


def _pdf_text(value):
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = text.encode("ascii", "replace").decode("ascii")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _exam_lines(exam):
    groups = ", ".join(group["name"] for group in exam["groups"])
    rooms = "; ".join(
        f'{room["name"]} ({room["id"]}): {room["exam_capacity"]} seats'
        for room in exam["rooms"]
    )
    fields = [
        f'EXAM: {exam["exam_id"]} | COURSE: {", ".join(exam["course_names"])}',
        (
            f'DAY: {exam["day"]} | DATE: not supplied by dataset | '
            f'SESSION: {exam["session"]} | '
            f'TIME: {exam["start_time"]}-{exam["end_time"]}'
        ),
        f'DURATION: {exam["duration_hours"]} hours | STUDENT GROUPS: {groups}',
        f'ROOMS: {rooms}',
        (
            f'ALLOCATED CAPACITY: {exam["total_allocated_capacity"]} seats '
            f'for {exam["student_count"]} students'
        ),
        "",
    ]
    return [
        line
        for field in fields
        for line in (textwrap.wrap(field, width=120) or [""])
    ]


def _pdf_object(object_id, value):
    return f"{object_id} 0 obj\n".encode("ascii") + value + b"\nendobj\n"


def generate_exam_timetable_pdf(schedule):
    lines = [
        "CIT NEXUS - EXAMINATION TIMETABLE",
        f'{schedule["exam_count"]} examinations | Solver: {schedule["solver_status"]}',
        "",
    ]
    for exam in schedule["exams"]:
        lines.extend(_exam_lines(exam))

    content_pages = [
        lines[start:start + LINES_PER_PAGE]
        for start in range(0, len(lines), LINES_PER_PAGE)
    ] or [[]]

    page_ids = [4 + index * 2 for index in range(len(content_pages))]
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
            f"/Count {len(page_ids)} >>"
        ).encode("ascii"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }

    for index, page_lines in enumerate(content_pages):
        page_id = page_ids[index]
        stream_id = page_id + 1
        commands = ["BT", "/F1 9 Tf", f"{LEFT_MARGIN} {TOP_MARGIN} Td"]
        for line_index, line in enumerate(page_lines):
            if line_index:
                commands.append(f"0 -{LINE_HEIGHT} Td")
            commands.append(f"({_pdf_text(line)}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode("ascii")
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>"
        ).encode("ascii")
        objects[stream_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id in range(1, max(objects) + 1):
        offsets.append(len(pdf))
        pdf.extend(_pdf_object(object_id, objects[object_id]))

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(pdf)
