"""
Study Planner
=============
Turns a student's mood check-in + their subjects' exam dates into a short,
concrete "what to study today" plan. No AI call needed — this is plain
rule-based scheduling.
"""
from datetime import date, timedelta

MOOD_OPTIONS = {
    "motivated": {
        "label": "Motivated",
        "icon": "flame",
        "session_minutes": 45,
        "tip": "Great headspace — tackle your toughest subject first.",
    },
    "focused": {
        "label": "Focused",
        "icon": "check-circle",
        "session_minutes": 40,
        "tip": "Steady focus — good time for structured revision.",
    },
    "neutral": {
        "label": "Okay",
        "icon": "meh",
        "session_minutes": 30,
        "tip": "A calm, regular session — ease in with a quick note review.",
    },
    "tired": {
        "label": "Tired",
        "icon": "frown",
        "session_minutes": 20,
        "tip": "Keep it short — a light 20-minute review beats skipping study.",
    },
    "stressed": {
        "label": "Stressed",
        "icon": "storm",
        "session_minutes": 15,
        "tip": "Short and calm — 15 focused minutes on one topic, then breathe.",
    },
}

DEFAULT_SESSION_MINUTES = 25


def exam_countdown_label(exam_date, today=None):
    """ 'Today' / 'Tomorrow' / 'in 3 days' / 'Passed', or None if no date. """
    if not exam_date:
        return None
    today = today or date.today()
    delta = (exam_date - today).days
    if delta < 0:
        return "Passed"
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return f"in {delta} days"


def build_study_plan(subjects, mood, today=None):
    """ subjects: list of Subject rows (already scoped to the student's
    section) with a `.notes` relationship and optional `.exam_date`.

    Returns a dict:
      { "mode": "exam_focus" | "normal" | "empty",
        "items": [{"subject": Subject, "minutes": int, "reason": str}, ...] }

    Exam-focus mode kicks in automatically whenever any subject's exam is
    tomorrow — the plan then narrows to ONLY that subject, per the
    requirement that an imminent exam should override normal rotation.
    """
    today = today or date.today()
    tomorrow = today + timedelta(days=1)

    session_minutes = MOOD_OPTIONS.get(mood, {}).get("session_minutes", DEFAULT_SESSION_MINUTES)

    exam_tomorrow = [s for s in subjects if s.exam_date == tomorrow]
    if exam_tomorrow:
        return {
            "mode": "exam_focus",
            "items": [
                {
                    "subject": s,
                    "minutes": session_minutes + 15,
                    "reason": "Exam is tomorrow — spend today's study time only on this subject.",
                }
                for s in exam_tomorrow
            ],
        }

    if not subjects:
        return {"mode": "empty", "items": []}

    # Normal rotation: subjects with the soonest upcoming exam first, undated
    # (or already-examined) subjects last, so only real upcoming exams get
    # priority. Subjects are included even if the teacher hasn't uploaded
    # any notes yet.
    def _rank_key(s):
        has_upcoming = s.exam_date is not None and s.exam_date >= today
        return (not has_upcoming, s.exam_date if has_upcoming else date.max)

    ranked = sorted(subjects, key=_rank_key)

    items = []
    for s in ranked:
        if s.exam_date and s.exam_date >= today:
            countdown = exam_countdown_label(s.exam_date, today)
            reason = f"Exam {countdown.lower()}" if countdown else "Regular revision"
        elif not s.notes:
            reason = "No notes uploaded yet — check back with your teacher"
        else:
            reason = "Regular revision"
        items.append({"subject": s, "minutes": session_minutes, "reason": reason})

    return {"mode": "normal", "items": items}
