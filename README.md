# AI-Based Smart Learning and Preparation System

**Live demo:** https://YOUR-APP-NAME.onrender.com  
_(free hosting sleeps when idle, so the first load can take up to a minute; the demo database resets on each restart)_

**Tech stack:** Python, Flask, Flask-SQLAlchemy, Flask-Login, SQLite/MySQL, Jinja2, Gemini API (optional), Gunicorn

A three-role Flask LMS: **Admin** manages accounts and structure, **Teacher**
publishes content and grades work, **Student** studies with an AI-driven,
mood-based plan.

## Roles

### Admin (`/admin/login`)
- Dashboard with total students, total teachers, total subjects, and active-user counts
- **Student Management** — add, edit, block/activate, delete; assign each student to a Section
- **Teacher Management** — add, edit, block/activate, delete
- **Subject Management** — create subjects, assign a Teacher and a Section, set an exam date
- **Section Management** — the class groups (e.g. "II MCA - A") students and subjects belong to
- **AI Settings** — paste a Gemini API key directly in the browser (no `.env` editing needed)
- **Reports** — per-subject average quiz scores, per-teacher activity (materials/quizzes/assignments created), and platform-wide study-plan usage

Admin creates every Teacher and Student login directly (name, username,
password) — there's no self-registration. Both can change their password
later from their own account.

### Teacher (`/teacher/login`)
- Dashboard: assigned students, subjects handled, upcoming exams, pending assignments, ungraded submissions, recent quiz attempts
- **Study materials** — upload PDF/DOCX/PPTX/TXT, add external video/resource links, or add an "important topic" note directly
- **Assignments** — create with a deadline, description, optional attachment and max marks; view and grade submissions with marks + feedback
- **Quizzes** — generate MCQs with Gemini from an uploaded material, or author them manually; set duration and an optional scheduled time
- **Announcements** — post a message that reaches every student in that subject's section
- All of the above automatically notify the relevant students

### Student (`/login`)
No self-registration — the Admin issues a username/password.

Dashboard includes:
- **Mood check-in** (Motivated / Focused / Okay / Tired / Stressed) — shapes today's plan
- **AI Study Plan** — auto-generated from mood + exam dates; if any subject's exam is **tomorrow**, the plan narrows to just that subject
- **Exam Countdown** — every subject with an exam date, Today/Tomorrow/"in N days"
- **Pending Assignments**, **My Subjects**, **AI Recommendations** (flags subjects averaging below 60% on quizzes)
- **Study Streak** (consecutive study days) and **weekly study minutes**
- **Study Timer** — server-tracked start/pause/stop with an animated progress ring; stopping it **auto-generates a quiz** from the material you just studied
- **Study Materials** — teacher-uploaded PDFs/notes/links, viewable in-browser
- **AI Notes/Summary** — Gemini turns a material into a structured summary or full study notes (headings, bolded key terms, possible exam questions)
- **Quizzes & Tests** — teacher-created, AI-generated from a note, or self-generated on demand; instant scoring and history
- **Assignments** — view, submit a file, see marks and teacher feedback once graded
- **Performance Analytics** (separate page) — subject-wise average scores, a weekly study-time bar chart, and a weak-subjects list
- **Notifications** — exam dates, new materials, new assignments, new quizzes, grades, and teacher announcements, all in one inbox
- **Floating AI Assistant** — a chat button in the bottom-right corner on every student page. Ask doubts, get explanations, or just chat; opening it from a material page scopes the conversation to that material

### Forgot password (security questions)

Works fully offline — no email needed.

- **Students** pick a security question from a drop-down and type the answer while registering.
- **Teachers** get a security question + answer when the admin creates the account.
- On the Student and Teacher login pages, **Forgot password?** asks for the
  registration number / username, shows that account's question, and — if the
  answer is right — lets the user set a new password. Answers are not case-sensitive
  and are stored hashed, never in plain text.
- After 5 wrong answers the reset is locked for 10 minutes.
- Existing accounts (created before this feature) can add a question from
  **Change Password → Security Question**, or the admin can set one from the
  Students / Teachers edit row. The database is upgraded automatically on startup.

## Full-screen AI Assistant (students)

Open it from the **AI Assistant** banner on the dashboard, the sidebar link, or the
expand button on the floating chat bubble. It works like the Gemini app:

- **Multiple chats** with history, search, pin, rename and delete (grouped by date).
- **Attach** images, PDF, Word, PowerPoint or text files (button, drag & drop, or paste).
  Images need a live AI key; documents work with or without one.
- **Tools** menu: explain simply, quiz me, summarize, flashcards, study plan,
  solve step by step, writing help, code help.
- **Class notes** picker: let the AI use a note your teacher uploaded as reference.
- Markdown answers with tables, syntax-highlighted code (with copy button) and maths (KaTeX).
- Copy, regenerate, edit your last message, like / dislike, read aloud, voice input,
  stop generating, download a chat as Markdown, light / dark mode and browser full screen.
- Shortcuts: `Enter` send, `Shift+Enter` new line, `/` focus the box, `Ctrl/Cmd+Shift+O` new chat.

It uses the same Gemini / Claude key as the rest of the app (Admin > AI Settings).
Without a key it falls back to the offline notes search. The page loads a few
libraries from a CDN (Markdown, sanitizer, code highlighting, maths); if the
internet is blocked, answers still show as plain text.

## Design

Flat, professional, high-contrast UI — no gradients, blur, or decorative
color effects. One accent color used only for actions and status. Full
light/dark mode. Below 900px width, the sidebar becomes a hamburger-triggered
off-canvas drawer.

## Getting started

```bash
pip install -r requirements.txt
python create_admin.py        # creates your Admin login
python app.py                  # runs on http://localhost:5000
```

Typical setup order:
1. Log in as admin → **Sections** → add a section (e.g. department + name)
2. **Teachers** → add a teacher (set username/password for them)
3. **Subjects** → create a subject, assign it to that teacher and section, optionally set an exam date
4. **Students** → add a student, assign them to the same section
5. Log in as the teacher → open the subject → upload a material, create an assignment or quiz
6. Log in as the student → check the dashboard, mood check-in, study timer, quizzes

### Enabling real Gemini AI (optional)

Everything works out of the box on a built-in offline engine. For real Gemini:
log in as admin → **AI Settings** → paste a free key from
https://aistudio.google.com/apikey → Save. Takes effect immediately.

Alternatively, copy `.env.example` to `.env` and set `GEMINI_API_KEY` there.

### Database

SQLite by default. For MySQL, set `USE_MYSQL=1` and the `MYSQL_*` variables
in `.env` — see `config.py`.

## Project structure

```
app.py                       All routes: Admin / Teacher / Student modules
models.py                     Admin, Teacher, Section, Student, Subject, Note,
                               Assignment, AssignmentSubmission, StudySession,
                               MoodCheckin, AIChatMessage, Quiz/QuizQuestion/
                               QuizAttempt, Notification, AppSetting
create_admin.py                 One-time script to create the admin login
config.py                       Configuration (DB, uploads, AI keys)
utils/
  llm_client.py                  Shared Gemini / Anthropic call wrapper
  quiz_generator.py               AI + offline MCQ generation
  ai_assistant.py                  Summarize / conversational Q&A
  document_reader.py               Extracts text from PDF/DOCX/PPTX/TXT
  office_converter.py              Optional LibreOffice DOCX/PPTX -> PDF preview
  study_planner.py                 Mood + exam-date -> study plan logic
templates/
  admin_*.html                     Admin pages
  teacher_*.html                   Teacher pages
  dashboard.html, subject_notes.html, note_view.html, analytics.html,
  quiz.html, quiz_result.html, notifications.html                   Student pages
  base.html                        Shared shell — nav, hamburger drawer, floating AI widget
static/css/style.css              Professional flat design system
```

## Run locally

```bash
python -m venv venv
venv\Scripts\activate          # Windows  (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
python create_admin.py         # create your first admin login (once)
python app.py
```

Open http://127.0.0.1:5000 and sign in at `/admin/login`.

Copy `.env.example` to `.env` to add an optional `GEMINI_API_KEY`. Never commit `.env`.

## Deploy on Render (free)

1. Push this repo to GitHub.
2. On render.com choose **New > Web Service** and select the repo (or use the included `render.yaml` Blueprint).
3. Build command: `pip install -r requirements.txt`  
   Start command: `gunicorn app:app`
4. Set environment variables:

| Key | Value |
|-----|-------|
| `PYTHON_VERSION` | `3.12.3` |
| `SECRET_KEY` | a long random string |
| `ADMIN_USERNAME` | first admin login |
| `ADMIN_PASSWORD` | first admin password |
| `GEMINI_API_KEY` | optional |

On startup the app creates all tables and, if `ADMIN_USERNAME` / `ADMIN_PASSWORD` are set,
a first admin account. Then log in at `/admin/login` and create sections, subjects, teachers and students.

Note: SQLite on Render's free tier is temporary storage, so data resets on every restart or redeploy.
For permanent data use a hosted MySQL database (`USE_MYSQL=1` plus the `MYSQL_*` variables in `config.py`).
