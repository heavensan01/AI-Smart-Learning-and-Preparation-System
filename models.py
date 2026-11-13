import os
from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ======================================================================
# Accounts
# ======================================================================

class Admin(UserMixin, db.Model):
    __tablename__ = "admins"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_id(self):
        return f"admin-{self.id}"

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)


class Teacher(UserMixin, db.Model):
    """ Created only by the Admin, with login credentials issued at
    creation time. Teachers manage study materials, assignments and
    quizzes for the subjects/sections the Admin assigns them to. """
    __tablename__ = "teachers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(160), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Forgot-password support (security question + hashed answer)
    security_question = db.Column(db.String(200), nullable=True)
    security_answer_hash = db.Column(db.String(255), nullable=True)

    subjects = db.relationship("Subject", backref="teacher", lazy="dynamic")

    def get_id(self):
        return f"teacher-{self.id}"

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def set_security_answer(self, question, raw_answer):
        from utils.security_questions import hash_answer
        self.security_question = question
        self.security_answer_hash = hash_answer(raw_answer)

    def check_security_answer(self, raw_answer):
        from utils.security_questions import check_answer
        return check_answer(self.security_answer_hash, raw_answer)

    @property
    def has_security_question(self):
        return bool(self.security_question and self.security_answer_hash)


class Section(db.Model):
    """ A class / section, e.g. 'II MCA - A'. Admin creates these; every
    Student and Subject belongs to exactly one. """
    __tablename__ = "sections"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    department = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    students = db.relationship("Student", backref="section", cascade="all, delete-orphan")
    subjects = db.relationship("Subject", backref="section", cascade="all, delete-orphan")

    def label(self):
        return f"{self.department} — {self.name}"


class Student(UserMixin, db.Model):
    """ Created only by the Admin, with login credentials issued at
    creation time. Can change their own password afterwards. """
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(160), nullable=True)
    register_number = db.Column(db.String(40), nullable=True)
    section_id = db.Column(db.Integer, db.ForeignKey("sections.id"), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    # Forgot-password support (security question + hashed answer)
    security_question = db.Column(db.String(200), nullable=True)
    security_answer_hash = db.Column(db.String(255), nullable=True)

    sessions = db.relationship("StudySession", backref="student", cascade="all, delete-orphan")

    def get_id(self):
        return f"student-{self.id}"

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def set_security_answer(self, question, raw_answer):
        from utils.security_questions import hash_answer
        self.security_question = question
        self.security_answer_hash = hash_answer(raw_answer)

    def check_security_answer(self, raw_answer):
        from utils.security_questions import check_answer
        return check_answer(self.security_answer_hash, raw_answer)

    @property
    def has_security_question(self):
        return bool(self.security_question and self.security_answer_hash)


class AppSetting(db.Model):
    """ Key-value store — used for the Admin-configurable Gemini API key. """
    __tablename__ = "app_settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ======================================================================
# Academics
# ======================================================================

class Subject(db.Model):
    """ Created by the Admin, assigned to one Teacher and one Section. """
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    section_id = db.Column(db.Integer, db.ForeignKey("sections.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=True)
    description = db.Column(db.String(255), nullable=True)
    exam_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    notes = db.relationship("Note", backref="subject", cascade="all, delete-orphan")
    assignments = db.relationship("Assignment", backref="subject", cascade="all, delete-orphan")
    sessions = db.relationship("StudySession", backref="subject", cascade="all, delete-orphan")


class Note(db.Model):
    """ Study material uploaded by a Teacher. """
    __tablename__ = "notes"

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    uploaded_by_teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    file_type = db.Column(db.String(10), default="pdf")
    kind = db.Column(db.String(20), default="material")  # material | link | topic
    external_url = db.Column(db.String(500), nullable=True)
    is_important_topic = db.Column(db.Boolean, default=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    extracted_text = db.Column(db.Text, nullable=True)
    ai_summary = db.Column(db.Text, nullable=True)
    summarized_at = db.Column(db.DateTime, nullable=True)
    ai_notes = db.Column(db.Text, nullable=True)
    ai_notes_at = db.Column(db.DateTime, nullable=True)

    pdf_preview_path = db.Column(db.String(500), nullable=True)
    pdf_preview_attempted = db.Column(db.Boolean, default=False)

    uploaded_by = db.relationship("Teacher")

    def size_label(self):
        try:
            n = os.path.getsize(self.filepath)
        except OSError:
            return ""
        for unit in ("B", "KB", "MB"):
            if n < 1024:
                return f"{n:.0f} {unit}"
            n /= 1024
        return f"{n:.1f} GB"


class Assignment(db.Model):
    """ Created by a Teacher for one Subject. """
    __tablename__ = "assignments"

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)
    attachment_path = db.Column(db.String(500), nullable=True)
    attachment_name = db.Column(db.String(255), nullable=True)
    max_marks = db.Column(db.Integer, default=100)
    deadline = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teacher = db.relationship("Teacher")
    submissions = db.relationship("AssignmentSubmission", backref="assignment", cascade="all, delete-orphan")


class AssignmentSubmission(db.Model):
    __tablename__ = "assignment_submissions"

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    file_path = db.Column(db.String(500), nullable=True)
    file_name = db.Column(db.String(255), nullable=True)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    marks = db.Column(db.Integer, nullable=True)
    feedback = db.Column(db.Text, nullable=True)
    graded_at = db.Column(db.DateTime, nullable=True)

    student = db.relationship("Student")


# ======================================================================
# Study activity
# ======================================================================

class StudySession(db.Model):
    """ Server-tracked study timer. """
    __tablename__ = "study_sessions"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    note_id = db.Column(db.Integer, db.ForeignKey("notes.id"), nullable=True)

    start_time = db.Column(db.DateTime, default=datetime.utcnow)
    end_time = db.Column(db.DateTime, nullable=True)
    duration_minutes = db.Column(db.Float, default=0)

    is_paused = db.Column(db.Boolean, default=False)
    accumulated_seconds = db.Column(db.Float, default=0)
    last_resume_at = db.Column(db.DateTime, nullable=True)

    note = db.relationship("Note")

    def current_elapsed_seconds(self):
        total = self.accumulated_seconds or 0
        if not self.is_paused and self.last_resume_at and self.end_time is None:
            total += (datetime.utcnow() - self.last_resume_at).total_seconds()
        return max(0, total)


class MoodCheckin(db.Model):
    __tablename__ = "mood_checkins"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    mood = db.Column(db.String(20), nullable=False)
    checkin_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("Student")


class AIChatMessage(db.Model):
    """ Floating AI assistant conversation. note_id is null for general
    chit-chat / doubt-clearing not tied to a specific note. """
    __tablename__ = "ai_chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    note_id = db.Column(db.Integer, db.ForeignKey("notes.id"), nullable=True)
    role = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    note = db.relationship("Note")


class AIConversation(db.Model):
    """ A saved chat in the full-screen AI Assistant (Gemini-style). Each
    student can have many; they can be renamed, pinned and deleted. """
    __tablename__ = "ai_conversations"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    title = db.Column(db.String(120), nullable=False, default="New chat")
    pinned = db.Column(db.Boolean, default=False)
    note_id = db.Column(db.Integer, db.ForeignKey("notes.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    student = db.relationship(
        "Student",
        backref=db.backref("ai_conversations", cascade="all, delete-orphan"),
    )
    messages = db.relationship(
        "AIConvMessage", backref="conversation",
        cascade="all, delete-orphan", order_by="AIConvMessage.id",
    )


class AIConvMessage(db.Model):
    """ One message inside an AIConversation. attachments_json holds a JSON
    list describing files attached to a *user* message (images are kept on
    disk, document text is stored so follow-up questions keep the context). """
    __tablename__ = "ai_conv_messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("ai_conversations.id"), nullable=False)
    role = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False)
    attachments_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Quiz(db.Model):
    """ source: 'teacher' (authored/scheduled by a teacher), 'ai' (teacher
    asked Gemini to generate it from a note), 'auto' (system-generated
    right after a student finishes a timed study session), or 'manual'
    (student generated it themselves on demand from a note). """
    __tablename__ = "quizzes"

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    note_id = db.Column(db.Integer, db.ForeignKey("notes.id"), nullable=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True)  # set for auto/manual per-student quizzes
    title = db.Column(db.String(160), nullable=True)
    source = db.Column(db.String(20), default="manual")
    duration_minutes = db.Column(db.Integer, nullable=True)
    scheduled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    questions = db.relationship("QuizQuestion", backref="quiz", cascade="all, delete-orphan")
    subject = db.relationship("Subject")
    note = db.relationship("Note")
    teacher = db.relationship("Teacher")


class QuizQuestion(db.Model):
    __tablename__ = "quiz_questions"

    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey("quizzes.id"), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(255))
    option_b = db.Column(db.String(255))
    option_c = db.Column(db.String(255))
    option_d = db.Column(db.String(255))
    correct_option = db.Column(db.String(1))


class QuizAttempt(db.Model):
    __tablename__ = "quiz_attempts"

    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey("quizzes.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    score = db.Column(db.Integer, default=0)
    total = db.Column(db.Integer, default=0)
    percent = db.Column(db.Float, default=0)
    result_label = db.Column(db.String(30))
    taken_at = db.Column(db.DateTime, default=datetime.utcnow)

    quiz = db.relationship("Quiz")
    student = db.relationship("Student")


class Notification(db.Model):
    """ recipient_type: 'student' or 'teacher'. Populated automatically
    when a teacher uploads material, posts an assignment, schedules a
    quiz, or sets an exam date; teachers can also post a free-text
    announcement to their section. """
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    recipient_type = db.Column(db.String(10), nullable=False)
    recipient_id = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(30), default="general")
    message = db.Column(db.String(500), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
