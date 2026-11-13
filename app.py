import os
import json
import time
import uuid
from datetime import datetime, date, timedelta

from flask import (
    Flask, render_template, redirect, url_for, request, flash, jsonify,
    send_from_directory, abort
)
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from werkzeug.utils import secure_filename

from config import Config
from models import (
    db, Admin, Teacher, Section, Student, AppSetting, Subject, Note,
    Assignment, AssignmentSubmission, StudySession, MoodCheckin,
    AIChatMessage, Quiz, QuizQuestion, QuizAttempt, Notification,
    AIConversation, AIConvMessage
)
from utils.quiz_generator import generate_smart_quiz_with_source, score_quiz, result_label
from utils.document_reader import extract_paragraphs, paragraphs_to_text, extract_pptx_slides
from utils.ai_assistant import summarize_text, answer_question
from utils.office_converter import convert_to_pdf as convert_office_to_pdf
from utils.study_planner import MOOD_OPTIONS, build_study_plan, exam_countdown_label
from utils.ai_chat import (
    MODE_HINTS, MAX_DOC_CHARS_EACH, MAX_DOC_CHARS_TOTAL, MAX_HISTORY_MESSAGES,
    doc_block, generate_reply, make_title,
)
from utils.security_questions import (
    SECURITY_QUESTIONS, validate_question_and_answer, is_valid_question,
)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "previews"), exist_ok=True)
    os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "assignments"), exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(app.config["UPLOAD_FOLDER"]), "ai_chat"), exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(__file__), "instance"), exist_ok=True)

    db.init_app(app)

    def _load_settings_into_config():
        try:
            for row in AppSetting.query.all():
                if row.value:
                    app.config[row.key] = row.value
        except Exception:
            pass

    def _ensure_security_columns():
        """Adds the security-question columns to databases created before the
        Forgot Password feature existed, so old data keeps working."""
        try:
            from sqlalchemy import inspect, text
            insp = inspect(db.engine)
            for table in ("teachers", "students"):
                if not insp.has_table(table):
                    continue  # fresh install: create_all() will build it
                existing = {c["name"] for c in insp.get_columns(table)}
                for col, ddl in (("security_question", "VARCHAR(200)"),
                                 ("security_answer_hash", "VARCHAR(255)")):
                    if col not in existing:
                        with db.engine.begin() as conn:
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        except Exception as exc:  # never block startup
            app.logger.warning("Security-question column check failed: %s", exc)

    with app.app_context():
        _ensure_security_columns()
        _load_settings_into_config()
    app.reload_ai_settings = _load_settings_into_config

    login_manager = LoginManager()
    login_manager.login_view = "student_login"
    login_manager.login_message = "Please log in to continue."
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(prefixed_id):
        try:
            kind, raw_id = prefixed_id.split("-", 1)
            raw_id = int(raw_id)
        except (ValueError, AttributeError):
            return None
        if kind == "admin":
            return Admin.query.get(raw_id)
        if kind == "teacher":
            return Teacher.query.get(raw_id)
        if kind == "student":
            return Student.query.get(raw_id)
        return None

    def is_admin():
        return current_user.is_authenticated and isinstance(current_user, Admin)

    def is_teacher():
        return current_user.is_authenticated and isinstance(current_user, Teacher)

    def is_student():
        return current_user.is_authenticated and isinstance(current_user, Student)

    def unread_notification_count():
        if is_student():
            return Notification.query.filter_by(
                recipient_type="student", recipient_id=current_user.id, is_read=False
            ).count()
        if is_teacher():
            return Notification.query.filter_by(
                recipient_type="teacher", recipient_id=current_user.id, is_read=False
            ).count()
        return 0

    @app.context_processor
    def inject_globals():
        active = None
        if is_student():
            active = StudySession.query.filter_by(
                student_id=current_user.id, end_time=None
            ).order_by(StudySession.id.desc()).first()
        ai_on = bool(app.config.get("GEMINI_API_KEY") or app.config.get("ANTHROPIC_API_KEY"))
        return {
            "global_active_session": active,
            "is_admin_user": is_admin(),
            "is_teacher_user": is_teacher(),
            "is_student_user": is_student(),
            "ai_backend_active": ai_on,
            "unread_notification_count": unread_notification_count(),
            "now": datetime.utcnow(),
        }

    def admin_required(fn):
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not is_admin():
                flash("Admin login required.", "error")
                return redirect(url_for("admin_login"))
            return fn(*args, **kwargs)
        return wrapper

    def teacher_required(fn):
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not is_teacher():
                flash("Teacher login required.", "error")
                return redirect(url_for("teacher_login"))
            return fn(*args, **kwargs)
        return wrapper

    def student_required(fn):
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not is_student():
                flash("Please log in to continue.", "error")
                return redirect(url_for("student_login"))
            return fn(*args, **kwargs)
        return wrapper

    def notify(recipient_type, recipient_id, message, category="general"):
        db.session.add(Notification(
            recipient_type=recipient_type, recipient_id=recipient_id,
            message=message, category=category,
        ))

    def notify_section_students(section_id, message, category="general"):
        for sid in [s.id for s in Student.query.filter_by(section_id=section_id, is_active=True).all()]:
            notify("student", sid, message, category)

    def _parse_date(value):
        if not value:
            return None
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except (ValueError, AttributeError):
            return None

    def _parse_datetime(value):
        if not value:
            return None
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%dT%H:%M")
        except (ValueError, AttributeError):
            return None

    # ------------------------------------------------------------------
    # Home / landing
    # ------------------------------------------------------------------
    @app.route("/")
    def index():
        if is_admin():
            return redirect(url_for("admin_dashboard"))
        if is_teacher():
            return redirect(url_for("teacher_dashboard"))
        if is_student():
            return redirect(url_for("dashboard"))
        return render_template("index.html")

    # ------------------------------------------------------------------
    # Login / logout — all three roles use admin-issued credentials
    # ------------------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def student_login():
        if is_admin():
            return redirect(url_for("admin_dashboard"))
        if is_teacher():
            return redirect(url_for("teacher_dashboard"))
        if is_student():
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            student = Student.query.filter_by(username=username).first()
            if not student or not student.check_password(password):
                flash("Invalid registration number or password.", "error")
                return redirect(url_for("student_login"))
            if not student.is_active:
                flash("This account has been deactivated. Contact your admin.", "error")
                return redirect(url_for("student_login"))

            student.last_login_at = datetime.utcnow()
            db.session.commit()
            login_user(student)
            flash(f"Welcome, {student.name}.", "success")
            return redirect(url_for("dashboard"))

        return render_template("student_login.html")

    @app.route("/register", methods=["GET", "POST"])
    def student_register():
        if is_admin():
            return redirect(url_for("admin_dashboard"))
        if is_teacher():
            return redirect(url_for("teacher_dashboard"))
        if is_student():
            return redirect(url_for("dashboard"))

        sections = Section.query.order_by(Section.department, Section.name).all()

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            register_number = (request.form.get("register_number") or "").strip()
            section_id = request.form.get("section_id", type=int)
            password = request.form.get("password") or ""
            confirm_password = request.form.get("confirm_password") or ""
            sec_question = request.form.get("security_question") or ""
            sec_answer = request.form.get("security_answer") or ""

            if not (name and register_number and section_id and password):
                flash("Name, register number, section and password are required.", "error")
                return redirect(url_for("student_register"))
            sec_error = validate_question_and_answer(sec_question, sec_answer)
            if sec_error:
                flash(sec_error, "error")
                return redirect(url_for("student_register"))
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
                return redirect(url_for("student_register"))
            if password != confirm_password:
                flash("Passwords do not match.", "error")
                return redirect(url_for("student_register"))
            if Student.query.filter_by(register_number=register_number).first():
                flash("An account with that register number already exists.", "error")
                return redirect(url_for("student_register"))

            student = Student(
                name=name, username=register_number, register_number=register_number,
                section_id=section_id,
            )
            student.set_password(password)
            student.set_security_answer(sec_question, sec_answer)
            db.session.add(student)
            db.session.commit()
            flash("Account created. You can now log in.", "success")
            return redirect(url_for("student_login"))

        return render_template(
            "student_register.html", sections=sections,
            security_questions=SECURITY_QUESTIONS,
        )

    @app.route("/teacher/login", methods=["GET", "POST"])
    def teacher_login():
        if is_admin() or is_student():
            logout_user()
        if is_teacher():
            return redirect(url_for("teacher_dashboard"))

        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            teacher = Teacher.query.filter_by(username=username).first()
            if not teacher or not teacher.check_password(password):
                flash("Invalid username or password.", "error")
                return redirect(url_for("teacher_login"))
            if not teacher.is_active:
                flash("This account has been deactivated. Contact your admin.", "error")
                return redirect(url_for("teacher_login"))

            login_user(teacher)
            flash(f"Welcome back, {teacher.name}.", "success")
            return redirect(url_for("teacher_dashboard"))

        return render_template("teacher_login.html")

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if is_teacher() or is_student():
            logout_user()
        if is_admin():
            return redirect(url_for("admin_dashboard"))

        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            admin = Admin.query.filter_by(username=username).first()
            if admin and admin.check_password(password):
                login_user(admin)
                flash(f"Welcome back, {admin.name}.", "success")
                return redirect(url_for("admin_dashboard"))
            flash("Invalid admin username or password.", "error")
            return redirect(url_for("admin_login"))

        return render_template("admin_login.html")

    @app.route("/logout")
    def logout():
        logout_user()
        flash("Logged out.", "success")
        return redirect(url_for("index"))

    # ------------------------------------------------------------------
    # Forgot password (security question) — students and teachers
    # ------------------------------------------------------------------
    RESET_MAX_ATTEMPTS = 5
    RESET_LOCK_SECONDS = 10 * 60
    reset_attempts = {}   # (role, username) -> [failed_count, locked_until_ts]

    def _reset_locked_for(key):
        entry = reset_attempts.get(key)
        if entry and entry[1] > time.time():
            return int((entry[1] - time.time()) // 60) + 1
        if entry and entry[1] and entry[1] <= time.time():
            reset_attempts.pop(key, None)
        return 0

    def _reset_register_failure(key):
        entry = reset_attempts.setdefault(key, [0, 0])
        entry[0] += 1
        if entry[0] >= RESET_MAX_ATTEMPTS:
            entry[1] = time.time() + RESET_LOCK_SECONDS
            entry[0] = 0

    def _forgot_password(role):
        if current_user.is_authenticated:
            return redirect(url_for("index"))

        is_student_role = role == "student"
        Model = Student if is_student_role else Teacher
        login_endpoint = "student_login" if is_student_role else "teacher_login"
        ctx = dict(
            role=role,
            role_label="Student" if is_student_role else "Teacher",
            username_label="Registration Number" if is_student_role else "Username",
            login_endpoint=login_endpoint,
            question=None,
            username="",
        )

        if request.method == "GET":
            return render_template("forgot_password.html", **ctx)

        step = request.form.get("step")
        username = (request.form.get("username") or "").strip()
        ctx["username"] = username
        account = Model.query.filter_by(username=username).first() if username else None

        # ---- Step 1: look the account up and show its question ----------
        if step == "lookup":
            if not account:
                flash(f"No {ctx['role_label'].lower()} account found with that "
                      f"{ctx['username_label'].lower()}.", "error")
            elif not account.is_active:
                flash("This account has been deactivated. Contact your admin.", "error")
            elif not account.has_security_question:
                flash("No security question is set for this account. "
                      "Please ask your admin to reset your password.", "error")
            else:
                ctx["question"] = account.security_question
            return render_template("forgot_password.html", **ctx)

        # ---- Step 2: verify the answer and set the new password ---------
        if step == "reset":
            if not account or not account.is_active or not account.has_security_question:
                flash("Unable to reset this password. Please contact your admin.", "error")
                ctx["username"] = ""
                return render_template("forgot_password.html", **ctx)

            ctx["question"] = account.security_question
            key = (role, username.lower())

            minutes = _reset_locked_for(key)
            if minutes:
                flash(f"Too many incorrect answers. Try again in about {minutes} minute(s), "
                      "or ask your admin to reset your password.", "error")
                return render_template("forgot_password.html", **ctx)

            answer = request.form.get("security_answer") or ""
            new_pw = request.form.get("new_password") or ""
            confirm_pw = request.form.get("confirm_password") or ""

            if not account.check_security_answer(answer):
                _reset_register_failure(key)
                flash("That answer is incorrect.", "error")
            elif len(new_pw) < 6:
                flash("New password must be at least 6 characters.", "error")
            elif new_pw != confirm_pw:
                flash("New passwords do not match.", "error")
            else:
                account.set_password(new_pw)
                db.session.commit()
                reset_attempts.pop(key, None)
                flash("Password reset successfully. You can now log in with your new password.",
                      "success")
                return redirect(url_for(login_endpoint))
            return render_template("forgot_password.html", **ctx)

        return render_template("forgot_password.html", **ctx)

    @app.route("/forgot-password/student", methods=["GET", "POST"])
    def student_forgot_password():
        return _forgot_password("student")

    @app.route("/forgot-password/teacher", methods=["GET", "POST"])
    def teacher_forgot_password():
        return _forgot_password("teacher")

    @app.route("/account/security-question", methods=["POST"])
    def update_security_question():
        if not (is_teacher() or is_student()):
            flash("Please log in to continue.", "error")
            return redirect(url_for("student_login"))

        current_pw = request.form.get("current_password") or ""
        question = request.form.get("security_question") or ""
        answer = request.form.get("security_answer") or ""

        error = validate_question_and_answer(question, answer)
        if not current_user.check_password(current_pw):
            flash("Current password is incorrect.", "error")
        elif error:
            flash(error, "error")
        else:
            current_user.set_security_answer(question, answer)
            db.session.commit()
            flash("Security question updated.", "success")
        return redirect(url_for("change_password"))

    @app.route("/account/change-password", methods=["GET", "POST"])
    def change_password():
        if not (is_teacher() or is_student()):
            flash("Please log in to continue.", "error")
            return redirect(url_for("student_login"))

        if request.method == "POST":
            current_pw = request.form.get("current_password") or ""
            new_pw = request.form.get("new_password") or ""
            confirm_pw = request.form.get("confirm_password") or ""

            if not current_user.check_password(current_pw):
                flash("Current password is incorrect.", "error")
            elif len(new_pw) < 4:
                flash("New password must be at least 4 characters.", "error")
            elif new_pw != confirm_pw:
                flash("New passwords do not match.", "error")
            else:
                current_user.set_password(new_pw)
                db.session.commit()
                flash("Password changed successfully.", "success")
                return redirect(url_for("dashboard" if is_student() else "teacher_dashboard"))

        return render_template("change_password.html", security_questions=SECURITY_QUESTIONS)

    # ==================================================================
    # ADMIN MODULE
    # ==================================================================
    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        total_students = Student.query.count()
        total_teachers = Teacher.query.count()
        total_subjects = Subject.query.count()
        active_students = Student.query.filter_by(is_active=True).count()
        active_teachers = Teacher.query.filter_by(is_active=True).count()
        upcoming_exams = Subject.query.filter(
            Subject.exam_date != None, Subject.exam_date >= date.today()
        ).order_by(Subject.exam_date).limit(6).all()
        return render_template(
            "admin_dashboard.html",
            total_students=total_students, total_teachers=total_teachers,
            total_subjects=total_subjects, active_students=active_students,
            active_teachers=active_teachers, upcoming_exams=upcoming_exams,
        )

    # ---- Sections -----------------------------------------------------
    @app.route("/admin/sections", methods=["GET", "POST"])
    @admin_required
    def admin_sections():
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            department = (request.form.get("department") or "").strip()
            if not (name and department):
                flash("Enter a section name and department.", "error")
            else:
                db.session.add(Section(name=name, department=department))
                db.session.commit()
                flash("Section added.", "success")
            return redirect(url_for("admin_sections"))

        sections = Section.query.order_by(Section.department, Section.name).all()
        return render_template("admin_sections.html", sections=sections)

    @app.route("/admin/sections/<int:section_id>/delete", methods=["POST"])
    @admin_required
    def admin_section_delete(section_id):
        section = Section.query.get_or_404(section_id)
        db.session.delete(section)
        db.session.commit()
        flash("Section removed.", "success")
        return redirect(url_for("admin_sections"))

    # ---- Students -------------------------------------------------------
    @app.route("/admin/students", methods=["GET"])
    @admin_required
    def admin_students():
        sections = Section.query.order_by(Section.department, Section.name).all()

        section_filter = request.args.get("section_id", type=int)
        query = Student.query
        if section_filter:
            query = query.filter_by(section_id=section_filter)
        students = query.order_by(Student.name).all()
        return render_template(
            "admin_students.html", students=students, sections=sections,
            section_filter=section_filter, security_questions=SECURITY_QUESTIONS,
        )

    @app.route("/admin/students/<int:student_id>/edit", methods=["POST"])
    @admin_required
    def admin_student_edit(student_id):
        student = Student.query.get_or_404(student_id)
        student.name = (request.form.get("name") or student.name).strip()
        student.email = (request.form.get("email") or "").strip()
        new_register_number = (request.form.get("register_number") or "").strip()
        if new_register_number:
            student.register_number = new_register_number
            student.username = new_register_number
        section_id = request.form.get("section_id", type=int)
        if section_id:
            student.section_id = section_id
        new_password = request.form.get("password") or ""
        if new_password:
            student.set_password(new_password)
        sec_question = request.form.get("security_question") or ""
        sec_answer = request.form.get("security_answer") or ""
        if sec_answer.strip():
            sec_error = validate_question_and_answer(sec_question, sec_answer)
            if sec_error:
                flash(sec_error, "error")
                return redirect(url_for("admin_students"))
            student.set_security_answer(sec_question, sec_answer)
        db.session.commit()
        flash("Student updated.", "success")
        return redirect(url_for("admin_students"))

    @app.route("/admin/students/<int:student_id>/toggle", methods=["POST"])
    @admin_required
    def admin_student_toggle(student_id):
        student = Student.query.get_or_404(student_id)
        student.is_active = not student.is_active
        db.session.commit()
        flash(f"Student {'activated' if student.is_active else 'blocked'}.", "success")
        return redirect(url_for("admin_students"))

    @app.route("/admin/students/<int:student_id>/delete", methods=["POST"])
    @admin_required
    def admin_student_delete(student_id):
        student = Student.query.get_or_404(student_id)
        db.session.delete(student)
        db.session.commit()
        flash("Student deleted.", "success")
        return redirect(url_for("admin_students"))

    # ---- Teachers -------------------------------------------------------
    @app.route("/admin/teachers", methods=["GET", "POST"])
    @admin_required
    def admin_teachers():
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            email = (request.form.get("email") or "").strip()
            sec_question = request.form.get("security_question") or ""
            sec_answer = request.form.get("security_answer") or ""
            sec_error = validate_question_and_answer(sec_question, sec_answer)

            if not (name and username and password):
                flash("Name, username and password are required.", "error")
            elif sec_error:
                flash(sec_error, "error")
            elif Teacher.query.filter_by(username=username).first():
                flash("That username is already taken.", "error")
            else:
                teacher = Teacher(name=name, username=username, email=email)
                teacher.set_password(password)
                teacher.set_security_answer(sec_question, sec_answer)
                db.session.add(teacher)
                db.session.commit()
                flash(f"Teacher '{name}' created.", "success")
            return redirect(url_for("admin_teachers"))

        teachers = Teacher.query.order_by(Teacher.name).all()
        return render_template(
            "admin_teachers.html", teachers=teachers,
            security_questions=SECURITY_QUESTIONS,
        )

    @app.route("/admin/teachers/<int:teacher_id>/edit", methods=["POST"])
    @admin_required
    def admin_teacher_edit(teacher_id):
        teacher = Teacher.query.get_or_404(teacher_id)
        teacher.name = (request.form.get("name") or teacher.name).strip()
        teacher.email = (request.form.get("email") or "").strip()
        new_password = request.form.get("password") or ""
        if new_password:
            teacher.set_password(new_password)
        sec_question = request.form.get("security_question") or ""
        sec_answer = request.form.get("security_answer") or ""
        if sec_answer.strip():
            sec_error = validate_question_and_answer(sec_question, sec_answer)
            if sec_error:
                flash(sec_error, "error")
                return redirect(url_for("admin_teachers"))
            teacher.set_security_answer(sec_question, sec_answer)
        db.session.commit()
        flash("Teacher updated.", "success")
        return redirect(url_for("admin_teachers"))

    @app.route("/admin/teachers/<int:teacher_id>/toggle", methods=["POST"])
    @admin_required
    def admin_teacher_toggle(teacher_id):
        teacher = Teacher.query.get_or_404(teacher_id)
        teacher.is_active = not teacher.is_active
        db.session.commit()
        flash(f"Teacher {'activated' if teacher.is_active else 'blocked'}.", "success")
        return redirect(url_for("admin_teachers"))

    @app.route("/admin/teachers/<int:teacher_id>/delete", methods=["POST"])
    @admin_required
    def admin_teacher_delete(teacher_id):
        teacher = Teacher.query.get_or_404(teacher_id)
        db.session.delete(teacher)
        db.session.commit()
        flash("Teacher deleted.", "success")
        return redirect(url_for("admin_teachers"))

    # ---- Subjects (assign teacher + section) -----------------------------
    @app.route("/admin/subjects", methods=["GET", "POST"])
    @admin_required
    def admin_subjects():
        sections = Section.query.order_by(Section.department, Section.name).all()
        teachers = Teacher.query.filter_by(is_active=True).order_by(Teacher.name).all()

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            section_id = request.form.get("section_id", type=int)
            teacher_id = request.form.get("teacher_id", type=int) or None
            description = (request.form.get("description") or "").strip()
            exam_date = _parse_date(request.form.get("exam_date"))

            if not (name and section_id):
                flash("Enter a subject name and choose a section.", "error")
            elif Subject.query.filter(
                Subject.section_id == section_id, db.func.lower(Subject.name) == name.lower()
            ).first():
                flash("A subject with that name already exists for this section.", "error")
            else:
                db.session.add(Subject(
                    name=name, section_id=section_id, teacher_id=teacher_id,
                    description=description, exam_date=exam_date,
                ))
                db.session.commit()
                flash("Subject added.", "success")
            return redirect(url_for("admin_subjects"))

        subjects = Subject.query.order_by(Subject.created_at.desc()).all()
        return render_template(
            "admin_subjects.html", subjects=subjects, sections=sections, teachers=teachers,
        )

    @app.route("/admin/subjects/<int:subject_id>/edit", methods=["POST"])
    @admin_required
    def admin_subject_edit(subject_id):
        subject = Subject.query.get_or_404(subject_id)
        new_name = (request.form.get("name") or subject.name).strip()
        section_id = request.form.get("section_id", type=int) or subject.section_id

        duplicate = Subject.query.filter(
            Subject.section_id == section_id, db.func.lower(Subject.name) == new_name.lower(),
            Subject.id != subject.id,
        ).first()
        if duplicate:
            flash("A subject with that name already exists for this section.", "error")
            return redirect(url_for("admin_subjects"))

        subject.name = new_name
        subject.section_id = section_id
        subject.teacher_id = request.form.get("teacher_id", type=int) or None
        subject.description = (request.form.get("description") or "").strip()
        if "exam_date" in request.form:
            subject.exam_date = _parse_date(request.form.get("exam_date"))
        db.session.commit()

        if subject.exam_date:
            notify_section_students(
                subject.section_id,
                f"Exam date set for {subject.name}: {subject.exam_date.strftime('%d %b %Y')}.",
                category="exam",
            )
            db.session.commit()

        flash("Subject updated.", "success")
        return redirect(url_for("admin_subjects"))

    @app.route("/admin/subjects/<int:subject_id>/delete", methods=["POST"])
    @admin_required
    def admin_subject_delete(subject_id):
        subject = Subject.query.get_or_404(subject_id)
        db.session.delete(subject)
        db.session.commit()
        flash("Subject removed.", "success")
        return redirect(url_for("admin_subjects"))

    # ---- Exam Dates (grouped by department) --------------------------------
    @app.route("/admin/exams")
    @admin_required
    def admin_exams():
        department_filter = (request.args.get("department") or "").strip()
        departments = [
            d[0] for d in db.session.query(Section.department).distinct().order_by(Section.department).all()
        ]

        query = Subject.query.join(Section).order_by(Section.department, Section.name, Subject.name)
        if department_filter:
            query = query.filter(Section.department == department_filter)
        subjects = query.all()

        grouped = {}
        for s in subjects:
            grouped.setdefault(s.section.department, []).append(s)

        return render_template(
            "admin_exams.html", grouped=grouped, departments=departments,
            department_filter=department_filter,
        )

    @app.route("/admin/exams/<int:subject_id>/set", methods=["POST"])
    @admin_required
    def admin_exam_set(subject_id):
        subject = Subject.query.get_or_404(subject_id)
        subject.exam_date = _parse_date(request.form.get("exam_date"))
        db.session.commit()

        if subject.exam_date:
            notify_section_students(
                subject.section_id,
                f"Exam date set for {subject.name}: {subject.exam_date.strftime('%d %b %Y')}.",
                category="exam",
            )
            db.session.commit()

        flash(f"Exam date updated for {subject.name}.", "success")
        return redirect(url_for("admin_exams", department=request.form.get("department_filter", "")))

    # ---- AI Settings ------------------------------------------------------
    @app.route("/admin/settings", methods=["GET", "POST"])
    @admin_required
    def admin_settings():
        if request.method == "POST":
            gemini_key = (request.form.get("gemini_api_key") or "").strip()
            if gemini_key:
                row = AppSetting.query.get("GEMINI_API_KEY")
                if not row:
                    row = AppSetting(key="GEMINI_API_KEY")
                    db.session.add(row)
                row.value = gemini_key
                db.session.commit()
                app.reload_ai_settings()
                flash("Gemini API key saved.", "success")
            else:
                flash("No key entered — left your current key unchanged.", "error")
            return redirect(url_for("admin_settings"))

        current_key = app.config.get("GEMINI_API_KEY", "")
        masked_key = (current_key[:4] + "…" + current_key[-4:]) if len(current_key) > 10 else current_key
        return render_template("admin_settings.html", has_key=bool(current_key), masked_key=masked_key)

    @app.route("/admin/settings/clear", methods=["POST"])
    @admin_required
    def admin_settings_clear():
        row = AppSetting.query.get("GEMINI_API_KEY")
        if row:
            db.session.delete(row)
            db.session.commit()
        app.config["GEMINI_API_KEY"] = ""
        flash("Gemini API key removed — offline engine will be used.", "success")
        return redirect(url_for("admin_settings"))

    # ---- Reports ------------------------------------------------------
    @app.route("/admin/reports")
    @admin_required
    def admin_reports():
        subjects = Subject.query.all()
        subject_stats = []
        for s in subjects:
            attempts = QuizAttempt.query.join(Quiz).filter(Quiz.subject_id == s.id).all()
            avg = round(sum(a.percent for a in attempts) / len(attempts), 1) if attempts else None
            subject_stats.append({
                "subject": s, "attempts": len(attempts), "avg_percent": avg,
                "materials": len(s.notes), "assignments": len(s.assignments),
            })

        teacher_stats = []
        for t in Teacher.query.all():
            materials = Note.query.filter_by(uploaded_by_teacher_id=t.id).count()
            quizzes = Quiz.query.filter_by(teacher_id=t.id).count()
            assignments = Assignment.query.filter_by(teacher_id=t.id).count()
            teacher_stats.append({"teacher": t, "materials": materials, "quizzes": quizzes, "assignments": assignments})

        total_sessions = StudySession.query.filter(StudySession.end_time != None).count()
        total_minutes = db.session.query(db.func.sum(StudySession.duration_minutes)).scalar() or 0
        total_moods = MoodCheckin.query.count()

        return render_template(
            "admin_reports.html", subject_stats=subject_stats, teacher_stats=teacher_stats,
            total_sessions=total_sessions, total_minutes=round(total_minutes),
            total_moods=total_moods,
        )

    # ==================================================================
    # TEACHER MODULE
    # ==================================================================
    def _own_teacher_subject(subject_id):
        return Subject.query.filter_by(id=subject_id, teacher_id=current_user.id).first_or_404()

    def _extract_and_cache(note):
        if note.extracted_text is not None:
            return note.extracted_text
        paragraphs = extract_paragraphs(note.filepath, note.file_type)
        text = paragraphs_to_text(paragraphs)
        note.extracted_text = text
        db.session.commit()
        return text

    @app.route("/teacher")
    @teacher_required
    def teacher_dashboard():
        subjects = Subject.query.filter_by(teacher_id=current_user.id).order_by(Subject.name).all()
        subject_ids = [s.id for s in subjects]
        total_students = sum(Student.query.filter_by(section_id=s.section_id, is_active=True).count() for s in subjects)
        upcoming_exams = [s for s in subjects if s.exam_date and s.exam_date >= date.today()]
        upcoming_exams.sort(key=lambda s: s.exam_date)

        pending_assignments = Assignment.query.filter(
            Assignment.subject_id.in_(subject_ids) if subject_ids else False,
            Assignment.deadline >= datetime.utcnow(),
        ).order_by(Assignment.deadline).limit(6).all() if subject_ids else []

        ungraded_count = 0
        if subject_ids:
            assignment_ids = [a.id for a in Assignment.query.filter(Assignment.subject_id.in_(subject_ids)).all()]
            if assignment_ids:
                ungraded_count = AssignmentSubmission.query.filter(
                    AssignmentSubmission.assignment_id.in_(assignment_ids),
                    AssignmentSubmission.marks == None,
                ).count()

        recent_attempts = []
        if subject_ids:
            recent_attempts = (
                QuizAttempt.query.join(Quiz).filter(Quiz.subject_id.in_(subject_ids))
                .order_by(QuizAttempt.taken_at.desc()).limit(8).all()
            )

        return render_template(
            "teacher_dashboard.html", subjects=subjects, total_students=total_students,
            upcoming_exams=upcoming_exams, pending_assignments=pending_assignments,
            ungraded_count=ungraded_count, recent_attempts=recent_attempts,
        )

    @app.route("/teacher/students")
    @teacher_required
    def teacher_students():
        subjects = Subject.query.filter_by(teacher_id=current_user.id).order_by(Subject.name).all()
        section_ids = sorted({s.section_id for s in subjects})
        subject_ids = [s.id for s in subjects]

        students = Student.query.filter(
            Student.section_id.in_(section_ids) if section_ids else False
        ).order_by(Student.name).all() if section_ids else []

        rows = []
        for st in students:
            sessions = StudySession.query.filter_by(student_id=st.id).filter(
                StudySession.subject_id.in_(subject_ids)
            ).all() if subject_ids else []
            total_minutes = round(sum((sess.duration_minutes or 0) for sess in sessions))

            attempts = QuizAttempt.query.join(Quiz).filter(
                QuizAttempt.student_id == st.id, Quiz.subject_id.in_(subject_ids)
            ).all() if subject_ids else []
            avg_percent = round(sum(a.percent for a in attempts) / len(attempts), 1) if attempts else None

            today_mood = MoodCheckin.query.filter_by(
                student_id=st.id, checkin_date=date.today()
            ).first()

            active_session = StudySession.query.filter_by(student_id=st.id, end_time=None).first()

            rows.append({
                "student": st,
                "total_minutes": total_minutes,
                "quiz_attempts": len(attempts),
                "avg_percent": avg_percent,
                "mood_today": today_mood.mood if today_mood else None,
                "last_login_at": st.last_login_at,
                "is_studying_now": bool(active_session),
            })

        rows.sort(key=lambda r: r["total_minutes"], reverse=True)

        return render_template(
            "teacher_students.html", subjects=subjects, rows=rows,
        )

    @app.route("/teacher/subjects/<int:subject_id>")
    @teacher_required
    def teacher_subject_detail(subject_id):
        subject = _own_teacher_subject(subject_id)
        notes = Note.query.filter_by(subject_id=subject.id).order_by(Note.uploaded_at.desc()).all()
        assignments = Assignment.query.filter_by(subject_id=subject.id).order_by(Assignment.deadline.desc()).all()
        quizzes = Quiz.query.filter_by(subject_id=subject.id, teacher_id=current_user.id).order_by(Quiz.created_at.desc()).all()
        students = Student.query.filter_by(section_id=subject.section_id, is_active=True).order_by(Student.name).all()
        return render_template(
            "teacher_subject_detail.html", subject=subject, notes=notes,
            assignments=assignments, quizzes=quizzes, students=students,
        )

    # ---- Study materials -----------------------------------------------
    @app.route("/teacher/subjects/<int:subject_id>/materials/upload", methods=["POST"])
    @teacher_required
    def teacher_material_upload(subject_id):
        subject = _own_teacher_subject(subject_id)
        kind = request.form.get("kind", "material")

        if kind == "link":
            title = (request.form.get("link_title") or "").strip()
            url = (request.form.get("link_url") or "").strip()
            if not (title and url):
                flash("Enter a title and URL.", "error")
                return redirect(url_for("teacher_subject_detail", subject_id=subject.id))
            note = Note(
                subject_id=subject.id, uploaded_by_teacher_id=current_user.id,
                filename=title, filepath="", file_type="link", kind="link", external_url=url,
            )
            db.session.add(note)
            db.session.commit()
            notify_section_students(subject.section_id, f"New resource link added for {subject.name}: {title}", "material")
            db.session.commit()
            flash("Link added.", "success")
            return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

        if kind == "topic":
            title = (request.form.get("topic_title") or "").strip()
            body = (request.form.get("topic_body") or "").strip()
            if not title:
                flash("Enter a topic title.", "error")
                return redirect(url_for("teacher_subject_detail", subject_id=subject.id))
            note = Note(
                subject_id=subject.id, uploaded_by_teacher_id=current_user.id,
                filename=title, filepath="", file_type="topic", kind="topic",
                extracted_text=body, is_important_topic=True,
            )
            db.session.add(note)
            db.session.commit()
            notify_section_students(subject.section_id, f"Important topic added for {subject.name}: {title}", "material")
            db.session.commit()
            flash("Important topic added.", "success")
            return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

        file = request.files.get("note_file")
        if not file or file.filename == "":
            flash("Choose a file to upload.", "error")
            return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

        filename = secure_filename(file.filename)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in Config.ALLOWED_NOTE_EXTENSIONS:
            flash("Supported file types: PDF, DOCX, PPTX, TXT.", "error")
            return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

        unique_name = f"{subject.id}_{int(datetime.utcnow().timestamp())}_{filename}"
        filepath = os.path.join(Config.UPLOAD_FOLDER, unique_name)
        file.save(filepath)

        note = Note(
            subject_id=subject.id, uploaded_by_teacher_id=current_user.id,
            filename=filename, filepath=filepath, file_type=ext, kind="material",
        )
        db.session.add(note)
        db.session.commit()
        _extract_and_cache(note)

        notify_section_students(subject.section_id, f"New study material for {subject.name}: {filename}", "material")
        db.session.commit()
        flash("Material uploaded.", "success")
        return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

    @app.route("/teacher/materials/<int:note_id>/delete", methods=["POST"])
    @teacher_required
    def teacher_material_delete(note_id):
        note = Note.query.get_or_404(note_id)
        subject = Subject.query.filter_by(id=note.subject_id, teacher_id=current_user.id).first_or_404()
        try:
            if note.filepath and os.path.exists(note.filepath):
                os.remove(note.filepath)
            if note.pdf_preview_path and os.path.exists(note.pdf_preview_path):
                os.remove(note.pdf_preview_path)
        except OSError:
            pass
        db.session.delete(note)
        db.session.commit()
        flash("Material removed.", "success")
        return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

    # ---- Assignments -----------------------------------------------
    @app.route("/teacher/subjects/<int:subject_id>/assignments/create", methods=["POST"])
    @teacher_required
    def teacher_assignment_create(subject_id):
        subject = _own_teacher_subject(subject_id)
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        max_marks = request.form.get("max_marks", type=int) or 100
        deadline = _parse_datetime(request.form.get("deadline"))

        if not (title and deadline):
            flash("Enter a title and deadline.", "error")
            return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

        attachment_path, attachment_name = None, None
        file = request.files.get("attachment")
        if file and file.filename:
            filename = secure_filename(file.filename)
            unique_name = f"assign_{subject.id}_{int(datetime.utcnow().timestamp())}_{filename}"
            attachment_path = os.path.join(Config.UPLOAD_FOLDER, "assignments", unique_name)
            file.save(attachment_path)
            attachment_name = filename

        assignment = Assignment(
            subject_id=subject.id, teacher_id=current_user.id, title=title,
            description=description, max_marks=max_marks, deadline=deadline,
            attachment_path=attachment_path, attachment_name=attachment_name,
        )
        db.session.add(assignment)
        db.session.commit()

        notify_section_students(subject.section_id, f"New assignment for {subject.name}: {title} (due {deadline.strftime('%d %b, %I:%M %p')})", "assignment")
        db.session.commit()
        flash("Assignment created.", "success")
        return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

    @app.route("/teacher/assignments/<int:assignment_id>")
    @teacher_required
    def teacher_assignment_detail(assignment_id):
        assignment = Assignment.query.get_or_404(assignment_id)
        subject = Subject.query.filter_by(id=assignment.subject_id, teacher_id=current_user.id).first_or_404()
        submissions = AssignmentSubmission.query.filter_by(assignment_id=assignment.id).order_by(
            AssignmentSubmission.submitted_at.desc()
        ).all()
        return render_template("teacher_assignment_detail.html", assignment=assignment, subject=subject, submissions=submissions)

    @app.route("/teacher/submissions/<int:submission_id>/grade", methods=["POST"])
    @teacher_required
    def teacher_submission_grade(submission_id):
        submission = AssignmentSubmission.query.get_or_404(submission_id)
        assignment = Assignment.query.filter_by(id=submission.assignment_id, teacher_id=current_user.id).first_or_404()
        marks = request.form.get("marks", type=int)
        feedback = (request.form.get("feedback") or "").strip()
        submission.marks = marks
        submission.feedback = feedback
        submission.graded_at = datetime.utcnow()
        db.session.commit()

        notify("student", submission.student_id, f"Your submission for '{assignment.title}' was graded: {marks}/{assignment.max_marks}.", "assignment")
        db.session.commit()
        flash("Submission graded.", "success")
        return redirect(url_for("teacher_assignment_detail", assignment_id=assignment.id))

    @app.route("/teacher/assignments/file/<int:assignment_id>")
    @teacher_required
    def teacher_assignment_file(assignment_id):
        assignment = Assignment.query.get_or_404(assignment_id)
        Subject.query.filter_by(id=assignment.subject_id, teacher_id=current_user.id).first_or_404()
        directory, fname = os.path.split(assignment.attachment_path)
        return send_from_directory(directory, fname, as_attachment=True, download_name=assignment.attachment_name)

    @app.route("/teacher/submissions/file/<int:submission_id>")
    @teacher_required
    def teacher_submission_file(submission_id):
        submission = AssignmentSubmission.query.get_or_404(submission_id)
        Assignment.query.filter_by(id=submission.assignment_id, teacher_id=current_user.id).first_or_404()
        directory, fname = os.path.split(submission.file_path)
        return send_from_directory(directory, fname, as_attachment=True, download_name=submission.file_name)

    # ---- Quizzes (manual MCQ authoring + Gemini-assisted generation) ------
    @app.route("/teacher/subjects/<int:subject_id>/quizzes/create_ai", methods=["POST"])
    @teacher_required
    def teacher_quiz_create_ai(subject_id):
        subject = _own_teacher_subject(subject_id)
        note_id = request.form.get("note_id", type=int)
        title = (request.form.get("title") or "").strip() or f"{subject.name} Quiz"
        duration_minutes = request.form.get("duration_minutes", type=int) or 15
        scheduled_at = _parse_datetime(request.form.get("scheduled_at"))
        num_questions = request.form.get("num_questions", type=int) or 8

        note = Note.query.filter_by(id=note_id, subject_id=subject.id).first() if note_id else None
        text = _extract_and_cache(note) if note else ""
        if not text or not text.strip():
            flash("Choose a material with readable text to generate questions from.", "error")
            return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

        questions, _source = generate_smart_quiz_with_source(text, num_questions=num_questions, config=app.config)
        if not questions:
            flash("Couldn't generate questions from that material.", "error")
            return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

        quiz = Quiz(
            subject_id=subject.id, note_id=note.id if note else None, teacher_id=current_user.id,
            title=title, source="ai", duration_minutes=duration_minutes, scheduled_at=scheduled_at,
        )
        db.session.add(quiz)
        db.session.flush()
        for q in questions:
            db.session.add(QuizQuestion(
                quiz_id=quiz.id, question_text=q["question_text"],
                option_a=q["option_a"], option_b=q["option_b"],
                option_c=q["option_c"], option_d=q["option_d"],
                correct_option=q["correct_option"],
            ))
        db.session.commit()

        when = f" on {scheduled_at.strftime('%d %b, %I:%M %p')}" if scheduled_at else ""
        notify_section_students(subject.section_id, f"New quiz for {subject.name}: {title}{when}.", "quiz")
        db.session.commit()

        flash(f"Quiz created with {len(questions)} questions.", "success")
        return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

    @app.route("/teacher/subjects/<int:subject_id>/quizzes/create_manual", methods=["POST"])
    @teacher_required
    def teacher_quiz_create_manual(subject_id):
        subject = _own_teacher_subject(subject_id)
        title = (request.form.get("title") or "").strip() or f"{subject.name} Quiz"
        duration_minutes = request.form.get("duration_minutes", type=int) or 15
        scheduled_at = _parse_datetime(request.form.get("scheduled_at"))

        quiz = Quiz(
            subject_id=subject.id, teacher_id=current_user.id, title=title,
            source="teacher", duration_minutes=duration_minutes, scheduled_at=scheduled_at,
        )
        db.session.add(quiz)
        db.session.flush()

        i = 0
        while request.form.get(f"question_{i}"):
            db.session.add(QuizQuestion(
                quiz_id=quiz.id,
                question_text=request.form.get(f"question_{i}"),
                option_a=request.form.get(f"option_a_{i}"),
                option_b=request.form.get(f"option_b_{i}"),
                option_c=request.form.get(f"option_c_{i}"),
                option_d=request.form.get(f"option_d_{i}"),
                correct_option=(request.form.get(f"correct_{i}") or "A").upper(),
            ))
            i += 1

        if i == 0:
            db.session.rollback()
            flash("Add at least one question.", "error")
            return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

        db.session.commit()
        when = f" on {scheduled_at.strftime('%d %b, %I:%M %p')}" if scheduled_at else ""
        notify_section_students(subject.section_id, f"New quiz for {subject.name}: {title}{when}.", "quiz")
        db.session.commit()

        flash(f"Quiz created with {i} question(s).", "success")
        return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

    @app.route("/teacher/quizzes/<int:quiz_id>/scores")
    @teacher_required
    def teacher_quiz_scores(quiz_id):
        quiz = Quiz.query.get_or_404(quiz_id)
        Subject.query.filter_by(id=quiz.subject_id, teacher_id=current_user.id).first_or_404()
        attempts = QuizAttempt.query.filter_by(quiz_id=quiz.id).order_by(QuizAttempt.percent.desc()).all()
        return render_template("teacher_quiz_scores.html", quiz=quiz, attempts=attempts)

    # ---- Announcements -----------------------------------------------
    @app.route("/teacher/subjects/<int:subject_id>/announce", methods=["POST"])
    @teacher_required
    def teacher_announce(subject_id):
        subject = _own_teacher_subject(subject_id)
        message = (request.form.get("message") or "").strip()
        if not message:
            flash("Enter an announcement message.", "error")
        else:
            notify_section_students(subject.section_id, f"{current_user.name} ({subject.name}): {message}", "announcement")
            db.session.commit()
            flash("Announcement sent to the section.", "success")
        return redirect(url_for("teacher_subject_detail", subject_id=subject.id))

    # ==================================================================
    # STUDENT MODULE
    # ==================================================================
    def _student_subjects():
        return Subject.query.filter_by(section_id=current_user.section_id).order_by(Subject.name).all()

    def _owned_note(note_id):
        note = Note.query.get_or_404(note_id)
        subject = Subject.query.filter_by(id=note.subject_id, section_id=current_user.section_id).first()
        if not subject:
            abort(404)
        return note, subject

    def _study_streak():
        """ Consecutive days (ending today or yesterday) with at least one
        completed study session. """
        session_dates = {
            row.start_time.date()
            for row in StudySession.query.filter_by(student_id=current_user.id).filter(
                StudySession.end_time != None
            ).all()
        }
        if not session_dates:
            return 0
        streak = 0
        cursor = date.today()
        if cursor not in session_dates:
            cursor -= timedelta(days=1)
        while cursor in session_dates:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def _weekly_study_minutes():
        week_start = date.today() - timedelta(days=6)
        rows = StudySession.query.filter_by(student_id=current_user.id).filter(
            StudySession.end_time != None, StudySession.start_time >= datetime.combine(week_start, datetime.min.time())
        ).all()
        by_day = {(week_start + timedelta(days=i)): 0 for i in range(7)}
        for r in rows:
            d = r.start_time.date()
            if d in by_day:
                by_day[d] += r.duration_minutes or 0
        return [{"date": d.strftime("%a"), "minutes": round(m)} for d, m in sorted(by_day.items())]

    def _weak_subjects(subjects):
        """ Subjects where the student's average quiz score is below 60%. """
        weak = []
        for s in subjects:
            attempts = QuizAttempt.query.join(Quiz).filter(
                Quiz.subject_id == s.id, QuizAttempt.student_id == current_user.id
            ).all()
            if attempts:
                avg = sum(a.percent for a in attempts) / len(attempts)
                if avg < 60:
                    weak.append({"subject": s, "avg_percent": round(avg, 1)})
        return weak

    @app.route("/dashboard")
    @student_required
    def dashboard():
        subjects = _student_subjects()
        note_counts = {s.id: Note.query.filter_by(subject_id=s.id, kind="material").count() for s in subjects}

        today = date.today()
        mood_row = MoodCheckin.query.filter_by(student_id=current_user.id, checkin_date=today).first()
        study_plan = build_study_plan(subjects, mood_row.mood if mood_row else None, today)

        exam_timetable = sorted(
            [s for s in subjects if s.exam_date and s.exam_date >= today], key=lambda s: s.exam_date
        )

        subject_ids = [s.id for s in subjects]
        pending_assignments = []
        if subject_ids:
            all_assignments = Assignment.query.filter(Assignment.subject_id.in_(subject_ids)).all()
            submitted_ids = {
                sub.assignment_id for sub in AssignmentSubmission.query.filter_by(student_id=current_user.id).all()
            }
            pending_assignments = sorted(
                [a for a in all_assignments if a.id not in submitted_ids and a.deadline >= datetime.utcnow()],
                key=lambda a: a.deadline,
            )[:6]

        recent_sessions = StudySession.query.filter_by(student_id=current_user.id).order_by(
            StudySession.start_time.desc()
        ).limit(5).all()
        total_minutes_today = sum(
            (s.duration_minutes or 0) for s in recent_sessions if s.start_time and s.start_time.date() == today
        )

        weak = _weak_subjects(subjects)
        streak = _study_streak()
        weekly_minutes = _weekly_study_minutes()

        recent_attempts = QuizAttempt.query.filter_by(student_id=current_user.id).order_by(
            QuizAttempt.taken_at.desc()
        ).limit(5).all()

        return render_template(
            "dashboard.html", subjects=subjects, note_counts=note_counts,
            mood_options=MOOD_OPTIONS, mood_today=mood_row.mood if mood_row else None,
            study_plan=study_plan, exam_timetable=exam_timetable,
            exam_countdown_label=exam_countdown_label, today=today,
            pending_assignments=pending_assignments, recent_sessions=recent_sessions,
            total_minutes_today=round(total_minutes_today), weak_subjects=weak,
            study_streak=streak, weekly_minutes=weekly_minutes, recent_attempts=recent_attempts,
        )

    @app.route("/mood/checkin", methods=["POST"])
    @student_required
    def mood_checkin():
        mood = (request.form.get("mood") or "").strip().lower()
        if mood not in MOOD_OPTIONS:
            flash("Please pick a valid mood.", "error")
            return redirect(url_for("dashboard"))

        today = date.today()
        row = MoodCheckin.query.filter_by(student_id=current_user.id, checkin_date=today).first()
        if row:
            row.mood = mood
        else:
            row = MoodCheckin(student_id=current_user.id, mood=mood, checkin_date=today)
            db.session.add(row)
        db.session.commit()
        flash(f"Mood logged as {MOOD_OPTIONS[mood]['label']} — today's study plan is ready.", "success")
        return redirect(url_for("dashboard"))

    # ---- My Subjects / Materials -----------------------------------------
    @app.route("/subjects/<int:subject_id>")
    @student_required
    def subject_notes(subject_id):
        subject = Subject.query.filter_by(id=subject_id, section_id=current_user.section_id).first_or_404()
        materials = Note.query.filter_by(subject_id=subject.id, kind="material").order_by(Note.uploaded_at.desc()).all()
        links = Note.query.filter_by(subject_id=subject.id, kind="link").order_by(Note.uploaded_at.desc()).all()
        topics = Note.query.filter_by(subject_id=subject.id, kind="topic").order_by(Note.uploaded_at.desc()).all()
        assignments = Assignment.query.filter_by(subject_id=subject.id).order_by(Assignment.deadline.desc()).all()
        submitted_ids = {
            sub.assignment_id for sub in AssignmentSubmission.query.filter_by(student_id=current_user.id).all()
        }
        quizzes = Quiz.query.filter_by(subject_id=subject.id).order_by(Quiz.created_at.desc()).all()

        attempts = QuizAttempt.query.join(Quiz).filter(
            Quiz.subject_id == subject.id, QuizAttempt.student_id == current_user.id
        ).all()
        progress_percent = round(sum(a.percent for a in attempts) / len(attempts)) if attempts else None

        active_session = StudySession.query.filter_by(student_id=current_user.id, end_time=None).first()

        return render_template(
            "subject_notes.html", subject=subject, materials=materials, links=links,
            topics=topics, assignments=assignments, submitted_ids=submitted_ids,
            quizzes=quizzes, progress_percent=progress_percent, active_session=active_session,
        )

    def _extract_and_cache_student(note):
        if note.extracted_text is not None:
            return note.extracted_text
        paragraphs = extract_paragraphs(note.filepath, note.file_type)
        text = paragraphs_to_text(paragraphs)
        note.extracted_text = text
        db.session.commit()
        return text

    @app.route("/notes/<int:note_id>")
    @student_required
    def note_view(note_id):
        note, subject = _owned_note(note_id)

        if note.kind == "link":
            return redirect(note.external_url)

        text = note.extracted_text if note.kind == "topic" else _extract_and_cache_student(note)

        preview_mode = "native"
        pptx_slides = None
        iframe_url = None

        if note.kind == "topic":
            preview_mode = "topic"
        elif note.file_type == "pdf":
            iframe_url = url_for("note_file", note_id=note.id)
        elif note.file_type in ("docx", "pptx"):
            if not note.pdf_preview_attempted:
                note.pdf_preview_attempted = True
                preview_dir = os.path.join(Config.UPLOAD_FOLDER, "previews")
                converted = convert_office_to_pdf(note.filepath, preview_dir)
                if converted:
                    note.pdf_preview_path = converted
                db.session.commit()

            if note.pdf_preview_path and os.path.exists(note.pdf_preview_path):
                iframe_url = url_for("note_file", note_id=note.id, preview=1)
            elif note.file_type == "docx":
                preview_mode = "docx_client"
            else:
                preview_mode = "pptx_cards"
                pptx_slides = extract_pptx_slides(note.filepath)
        else:
            preview_mode = "txt"

        active_session = StudySession.query.filter_by(
            student_id=current_user.id, note_id=note.id, end_time=None
        ).first()

        chat_messages = AIChatMessage.query.filter_by(
            student_id=current_user.id, note_id=note.id
        ).order_by(AIChatMessage.created_at.asc()).all()

        return render_template(
            "note_view.html", note=note, subject=subject, text=text,
            preview_mode=preview_mode, pptx_slides=pptx_slides, iframe_url=iframe_url,
            active_session=active_session, chat_messages=chat_messages,
        )

    @app.route("/notes/file/<int:note_id>")
    @student_required
    def note_file(note_id):
        note, _subject = _owned_note(note_id)

        if request.args.get("preview") == "1" and note.pdf_preview_path and os.path.exists(note.pdf_preview_path):
            directory, fname = os.path.split(note.pdf_preview_path)
            return send_from_directory(directory, fname, mimetype="application/pdf")

        directory, fname = os.path.split(note.filepath)
        mimetypes = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "txt": "text/plain",
        }
        return send_from_directory(
            directory, fname,
            mimetype=mimetypes.get(note.file_type, "application/octet-stream"),
            as_attachment=request.args.get("download") == "1",
            download_name=note.filename,
        )

    # ---- Study timer -----------------------------------------------
    @app.route("/study/start_subject/<int:subject_id>", methods=["POST"])
    @student_required
    def study_start_subject(subject_id):
        subject = Subject.query.filter_by(id=subject_id, section_id=current_user.section_id).first_or_404()
        existing = StudySession.query.filter_by(student_id=current_user.id, end_time=None).first()
        if existing:
            return jsonify({"error": "A session is already running.", "session_id": existing.id}), 400

        now = datetime.utcnow()
        session_row = StudySession(
            student_id=current_user.id, subject_id=subject.id, note_id=None,
            start_time=now, last_resume_at=now,
        )
        db.session.add(session_row)
        db.session.commit()
        return jsonify({"session_id": session_row.id, "elapsed_seconds": 0})

    @app.route("/study/start/<int:note_id>", methods=["POST"])
    @student_required
    def study_start(note_id):
        note, subject = _owned_note(note_id)
        existing = StudySession.query.filter_by(student_id=current_user.id, end_time=None).first()
        if existing:
            return jsonify({"error": "A session is already running.", "session_id": existing.id}), 400

        now = datetime.utcnow()
        session_row = StudySession(
            student_id=current_user.id, subject_id=subject.id, note_id=note.id,
            start_time=now, last_resume_at=now,
        )
        db.session.add(session_row)
        db.session.commit()
        return jsonify({"session_id": session_row.id, "elapsed_seconds": 0})

    @app.route("/study/toggle_pause/<int:session_id>", methods=["POST"])
    @student_required
    def study_toggle_pause(session_id):
        session_row = StudySession.query.filter_by(
            id=session_id, student_id=current_user.id, end_time=None
        ).first_or_404()

        now = datetime.utcnow()
        if session_row.is_paused:
            session_row.is_paused = False
            session_row.last_resume_at = now
        else:
            if session_row.last_resume_at:
                session_row.accumulated_seconds = (session_row.accumulated_seconds or 0) + \
                    (now - session_row.last_resume_at).total_seconds()
            session_row.is_paused = True
            session_row.last_resume_at = None

        db.session.commit()
        return jsonify({
            "is_paused": session_row.is_paused,
            "elapsed_seconds": session_row.current_elapsed_seconds(),
        })

    @app.route("/study/status")
    @student_required
    def study_status():
        session_row = StudySession.query.filter_by(
            student_id=current_user.id, end_time=None
        ).order_by(StudySession.id.desc()).first()
        if not session_row:
            return jsonify({"active": False})
        return jsonify({
            "active": True, "session_id": session_row.id,
            "note_id": session_row.note_id,
            "subject_id": session_row.subject_id,
            "note_name": session_row.note.filename if session_row.note else "",
            "subject_name": session_row.subject.name if session_row.subject else "",
            "is_paused": session_row.is_paused,
            "elapsed_seconds": session_row.current_elapsed_seconds(),
        })

    @app.route("/study/stop/<int:session_id>", methods=["POST"])
    @student_required
    def study_stop(session_id):
        session_row = StudySession.query.filter_by(
            id=session_id, student_id=current_user.id, end_time=None
        ).first_or_404()

        now = datetime.utcnow()
        if not session_row.is_paused and session_row.last_resume_at:
            session_row.accumulated_seconds = (session_row.accumulated_seconds or 0) + \
                (now - session_row.last_resume_at).total_seconds()
        session_row.end_time = now
        session_row.duration_minutes = round((session_row.accumulated_seconds or 0) / 60, 1)
        db.session.commit()

        note = session_row.note
        text = _extract_and_cache_student(note) if note else ""
        quiz_id = None
        if text and text.strip():
            questions, _source = generate_smart_quiz_with_source(text, num_questions=8, config=app.config)
            if questions:
                quiz = Quiz(
                    subject_id=session_row.subject_id, note_id=note.id, student_id=current_user.id,
                    title=f"{session_row.subject.name} — auto quiz", source="auto",
                )
                db.session.add(quiz)
                db.session.flush()
                for q in questions:
                    db.session.add(QuizQuestion(
                        quiz_id=quiz.id, question_text=q["question_text"],
                        option_a=q["option_a"], option_b=q["option_b"],
                        option_c=q["option_c"], option_d=q["option_d"],
                        correct_option=q["correct_option"],
                    ))
                db.session.commit()
                quiz_id = quiz.id

        return jsonify({
            "duration_minutes": session_row.duration_minutes,
            "quiz_id": quiz_id,
            "redirect": url_for("quiz_take", quiz_id=quiz_id) if quiz_id else None,
        })

    # ---- AI features on a note: summarize / AI notes / manual quiz --------
    @app.route("/notes/<int:note_id>/summarize", methods=["POST"])
    @student_required
    def note_summarize(note_id):
        note, subject = _owned_note(note_id)
        text = _extract_and_cache_student(note)
        summary = summarize_text(text, config=app.config)
        note.ai_summary = summary
        note.summarized_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"summary": summary})

    @app.route("/notes/<int:note_id>/ai_notes", methods=["POST"])
    @student_required
    def note_ai_notes(note_id):
        note, subject = _owned_note(note_id)
        text = _extract_and_cache_student(note)
        if not text or not text.strip():
            return jsonify({"error": "This material has no readable text to work from."}), 400

        from utils.llm_client import call_llm
        result = call_llm(
            system_prompt=(
                "You are a study-notes assistant. Turn the given material into "
                "clean, well-organized study notes: short headings, bullet points "
                "for key facts/definitions, bolded key terms using **term** markdown, "
                "and end with a short list of possible exam questions. Do not add "
                "unrelated information."
            ),
            user_prompt=f"Create study notes from this material:\n\n{text[:12000]}",
            config=app.config, max_tokens=1000,
        )
        if not result:
            summary = summarize_text(text, config=app.config)
            sentences = [s.strip() for s in summary.replace("\n", " ").split(". ") if s.strip()]
            result = "**Key Points**\n" + "\n".join(f"- {s.rstrip('.')}." for s in sentences)

        note.ai_notes = result
        note.ai_notes_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"ai_notes": result})

    @app.route("/notes/<int:note_id>/generate_quiz", methods=["POST"])
    @student_required
    def note_generate_quiz_manual(note_id):
        note, subject = _owned_note(note_id)
        text = _extract_and_cache_student(note)
        if not text or not text.strip():
            flash("This material has no readable text to generate a quiz from.", "error")
            return redirect(url_for("note_view", note_id=note.id))

        questions, _source = generate_smart_quiz_with_source(text, num_questions=8, config=app.config)
        if not questions:
            flash("Couldn't generate a quiz from this material.", "error")
            return redirect(url_for("note_view", note_id=note.id))

        quiz = Quiz(
            subject_id=subject.id, note_id=note.id, student_id=current_user.id,
            title=f"{subject.name} — self quiz", source="manual",
        )
        db.session.add(quiz)
        db.session.flush()
        for q in questions:
            db.session.add(QuizQuestion(
                quiz_id=quiz.id, question_text=q["question_text"],
                option_a=q["option_a"], option_b=q["option_b"],
                option_c=q["option_c"], option_d=q["option_d"],
                correct_option=q["correct_option"],
            ))
        db.session.commit()
        return redirect(url_for("quiz_take", quiz_id=quiz.id))

    # ---- Floating AI Assistant (doubts + chit-chat) -----------------------
    @app.route("/ai/ask", methods=["POST"])
    @student_required
    def ai_ask():
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        note_id = data.get("note_id")
        if not question:
            return jsonify({"error": "Type a question first."}), 400

        note = None
        text = ""
        subject_name = None
        if note_id:
            note = Note.query.get(note_id)
            if note:
                subject = Subject.query.filter_by(
                    id=note.subject_id, section_id=current_user.section_id
                ).first()
                if subject:
                    text = _extract_and_cache_student(note)
                    subject_name = subject.name
                else:
                    note = None

        history_rows = AIChatMessage.query.filter_by(
            student_id=current_user.id, note_id=note.id if note else None
        ).order_by(AIChatMessage.created_at.asc()).limit(Config.AI_CHAT_HISTORY_LIMIT).all()
        history = [{"role": m.role, "content": m.content} for m in history_rows]

        answer = answer_question(question, text, subject_name=subject_name, config=app.config, history=history)

        db.session.add(AIChatMessage(student_id=current_user.id, note_id=note.id if note else None, role="user", content=question))
        db.session.add(AIChatMessage(student_id=current_user.id, note_id=note.id if note else None, role="assistant", content=answer))
        db.session.commit()

        return jsonify({"answer": answer, "created_at": datetime.utcnow().isoformat()})

    @app.route("/ai/history")
    @student_required
    def ai_history():
        note_id = request.args.get("note_id", type=int)
        rows = AIChatMessage.query.filter_by(
            student_id=current_user.id, note_id=note_id
        ).order_by(AIChatMessage.created_at.asc()).all()
        return jsonify({"messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()} for m in rows
        ]})


    # ==================================================================
    # FULL-SCREEN AI ASSISTANT (Gemini-style chat) — students only
    # ==================================================================
    AI_FS_DIR = os.path.join(os.path.dirname(app.config["UPLOAD_FOLDER"]), "ai_chat")
    AI_IMAGE_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "webp": "image/webp", "gif": "image/gif"}
    AI_MAX_FILES = 4
    AI_MAX_FILE_BYTES = 10 * 1024 * 1024

    def _ai_conv_or_404(conv_id):
        conv = AIConversation.query.filter_by(id=conv_id, student_id=current_user.id).first()
        if not conv:
            abort(404)
        return conv

    def _ai_atts(msg):
        try:
            return json.loads(msg.attachments_json) if msg.attachments_json else []
        except ValueError:
            return []

    def _ai_conv_dict(c):
        return {"id": c.id, "title": c.title, "pinned": bool(c.pinned),
                "note_id": c.note_id,
                "updated_at": (c.updated_at or c.created_at).isoformat() + "Z"}

    def _ai_msg_dict(m):
        atts = []
        for i, a in enumerate(_ai_atts(m)):
            item = {"name": a.get("name"), "kind": a.get("kind")}
            if a.get("kind") == "image":
                item["url"] = url_for("ai_attachment", message_id=m.id, index=i)
            atts.append(item)
        return {"id": m.id, "role": m.role, "content": m.content,
                "attachments": atts,
                "created_at": (m.created_at or datetime.utcnow()).isoformat() + "Z"}

    def _ai_delete_files(messages):
        for m in messages:
            for a in _ai_atts(m):
                rel = a.get("path")
                if a.get("kind") == "image" and rel:
                    try:
                        os.remove(os.path.join(AI_FS_DIR, rel))
                    except OSError:
                        pass

    def _ai_note_context(note_id):
        """ Returns (note, subject_name, text) if the note belongs to the
        student's section, else (None, None, ""). """
        if not note_id:
            return None, None, ""
        note = Note.query.get(note_id)
        if not note:
            return None, None, ""
        subject = Subject.query.filter_by(
            id=note.subject_id, section_id=current_user.section_id
        ).first()
        if not subject:
            return None, None, ""
        try:
            text = _extract_and_cache_student(note)
        except Exception:
            text = ""
        return note, subject.name, text or ""

    @app.route("/ai/assistant")
    @student_required
    def ai_fullscreen():
        subjects = Subject.query.filter_by(
            section_id=current_user.section_id
        ).order_by(Subject.name).all()
        note_groups = []
        for sub in subjects:
            notes = Note.query.filter_by(subject_id=sub.id, kind="material").order_by(Note.id.desc()).all()
            if notes:
                note_groups.append({
                    "subject": sub.name,
                    "notes": [{"id": n.id, "name": n.filename} for n in notes],
                })
        subject_names = [s.name for s in subjects][:3]
        return render_template(
            "ai_fullscreen.html",
            note_groups=note_groups,
            subject_names=subject_names,
            modes=list(MODE_HINTS.keys()),
        )

    @app.route("/ai/api/conversations")
    @student_required
    def ai_api_conversations():
        q = (request.args.get("q") or "").strip()
        query = AIConversation.query.filter_by(student_id=current_user.id)
        if q:
            like = f"%{q}%"
            matching_ids = db.session.query(AIConvMessage.conversation_id).join(
                AIConversation, AIConversation.id == AIConvMessage.conversation_id
            ).filter(
                AIConversation.student_id == current_user.id,
                AIConvMessage.content.ilike(like),
            )
            query = query.filter(
                db.or_(AIConversation.title.ilike(like), AIConversation.id.in_(matching_ids))
            )
        convs = query.order_by(AIConversation.pinned.desc(), AIConversation.updated_at.desc()).limit(200).all()
        return jsonify({"conversations": [_ai_conv_dict(c) for c in convs]})

    @app.route("/ai/api/conversations/<int:conv_id>")
    @student_required
    def ai_api_conversation(conv_id):
        conv = _ai_conv_or_404(conv_id)
        return jsonify({"conversation": _ai_conv_dict(conv),
                        "messages": [_ai_msg_dict(m) for m in conv.messages]})

    @app.route("/ai/api/conversations/<int:conv_id>/rename", methods=["POST"])
    @student_required
    def ai_api_rename(conv_id):
        conv = _ai_conv_or_404(conv_id)
        title = " ".join(((request.get_json(silent=True) or {}).get("title") or "").split())
        if not title:
            return jsonify({"error": "Title can't be empty."}), 400
        conv.title = title[:100]
        db.session.commit()
        return jsonify({"conversation": _ai_conv_dict(conv)})

    @app.route("/ai/api/conversations/<int:conv_id>/pin", methods=["POST"])
    @student_required
    def ai_api_pin(conv_id):
        conv = _ai_conv_or_404(conv_id)
        conv.pinned = not conv.pinned
        db.session.commit()
        return jsonify({"conversation": _ai_conv_dict(conv)})

    @app.route("/ai/api/conversations/<int:conv_id>/delete", methods=["POST"])
    @student_required
    def ai_api_delete(conv_id):
        conv = _ai_conv_or_404(conv_id)
        _ai_delete_files(conv.messages)
        db.session.delete(conv)
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/ai/api/conversations/delete_all", methods=["POST"])
    @student_required
    def ai_api_delete_all():
        for conv in AIConversation.query.filter_by(student_id=current_user.id).all():
            _ai_delete_files(conv.messages)
            db.session.delete(conv)
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/ai/attachment/<int:message_id>/<int:index>")
    @student_required
    def ai_attachment(message_id, index):
        msg = AIConvMessage.query.get_or_404(message_id)
        if msg.conversation.student_id != current_user.id:
            abort(404)
        atts = _ai_atts(msg)
        if index < 0 or index >= len(atts) or atts[index].get("kind") != "image":
            abort(404)
        return send_from_directory(AI_FS_DIR, atts[index]["path"])

    def _ai_save_uploads(files):
        """ Validates + stores uploaded files. Returns (attachments, error). """
        files = [f for f in files if f and f.filename]
        if len(files) > AI_MAX_FILES:
            return [], f"You can attach up to {AI_MAX_FILES} files at a time."
        os.makedirs(AI_FS_DIR, exist_ok=True)
        saved = []
        for f in files:
            name = secure_filename(f.filename) or "file"
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            data = f.read()
            if len(data) > AI_MAX_FILE_BYTES:
                return [], f"'{name}' is larger than 10 MB."
            if ext in AI_IMAGE_EXT:
                rel = f"{current_user.id}_{uuid.uuid4().hex}.{ext}"
                with open(os.path.join(AI_FS_DIR, rel), "wb") as fh:
                    fh.write(data)
                saved.append({"kind": "image", "name": name,
                              "mime": AI_IMAGE_EXT[ext], "path": rel})
            elif ext in app.config["ALLOWED_NOTE_EXTENSIONS"]:
                tmp = os.path.join(AI_FS_DIR, f"tmp_{uuid.uuid4().hex}.{ext}")
                with open(tmp, "wb") as fh:
                    fh.write(data)
                try:
                    text = paragraphs_to_text(extract_paragraphs(tmp, ext))
                except Exception:
                    text = ""
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                if not text.strip():
                    return [], f"I couldn't read any text from '{name}'."
                saved.append({"kind": "doc", "name": name, "text": text[:MAX_DOC_CHARS_EACH]})
            else:
                return [], f"'{name}' isn't supported. Attach images, PDF, DOCX, PPTX or TXT."
        return saved, None

    def _ai_history_for_llm(messages):
        """ Converts stored messages to [{role, content}], re-attaching the
        text of earlier documents (newest first, within a size budget) so
        follow-up questions keep their context. """
        budget = MAX_DOC_CHARS_TOTAL
        rows = list(messages)[-MAX_HISTORY_MESSAGES:]
        out = []
        for m in reversed(rows):
            content = m.content
            if m.role == "user":
                blocks = ""
                for a in _ai_atts(m):
                    if a.get("kind") == "doc" and budget > 0:
                        chunk = (a.get("text") or "")[:min(MAX_DOC_CHARS_EACH, budget)]
                        budget -= len(chunk)
                        blocks += doc_block(a.get("name"), chunk)
                content = blocks + content
            out.append({"role": m.role, "content": content})
        out.reverse()
        return out

    @app.route("/ai/api/chat", methods=["POST"])
    @student_required
    def ai_api_chat():
        form = request.form
        message = (form.get("message") or "").strip()
        conv_id = form.get("conversation_id", type=int)
        note_id = form.get("note_id", type=int)
        mode = (form.get("mode") or "").strip()
        regenerate = form.get("regenerate") == "1"
        edit_id = form.get("edit_message_id", type=int)

        conv = None
        if conv_id:
            conv = _ai_conv_or_404(conv_id)

        attachments = []
        user_msg = None

        if regenerate:
            if not conv:
                return jsonify({"error": "Nothing to regenerate."}), 400
            user_msg = next((m for m in reversed(conv.messages) if m.role == "user"), None)
            if not user_msg:
                return jsonify({"error": "Nothing to regenerate."}), 400
            for m in [m for m in conv.messages if m.id > user_msg.id]:
                db.session.delete(m)
            db.session.flush()
            db.session.expire(conv, ["messages"])
            message = user_msg.content
            attachments = _ai_atts(user_msg)
        else:
            new_files, err = _ai_save_uploads(request.files.getlist("files"))
            if err:
                return jsonify({"error": err}), 400

            if edit_id and conv:
                old = AIConvMessage.query.filter_by(
                    id=edit_id, conversation_id=conv.id, role="user").first()
                if old:
                    if not new_files:
                        attachments = _ai_atts(old)
                    doomed = [m for m in conv.messages if m.id >= old.id]
                    kept_paths = {a.get("path") for a in attachments}
                    _ai_delete_files([m for m in doomed
                                      if not any(a.get("path") in kept_paths for a in _ai_atts(m))])
                    for m in doomed:
                        db.session.delete(m)
                    db.session.flush()
                    db.session.expire(conv, ["messages"])
            attachments = attachments + new_files

            if not message and not attachments:
                return jsonify({"error": "Type a message first."}), 400
            if not message:
                message = "Please look at the attached file(s) and explain what's in them."

        if not conv:
            conv = AIConversation(student_id=current_user.id, title=make_title(message))
            db.session.add(conv)
            db.session.flush()
        elif conv.title in ("", "New chat") and not conv.messages:
            conv.title = make_title(message)

        # Optional notes context for this chat
        note, subject_name, note_text = _ai_note_context(note_id)
        conv.note_id = note.id if note else None

        # History = everything before the message we're answering
        prior = [m for m in conv.messages if not (user_msg and m.id >= user_msg.id)]
        history = _ai_history_for_llm(prior)

        # Current turn's documents + images
        doc_blocks, images = [], []
        for a in attachments:
            if a.get("kind") == "doc":
                doc_blocks.append(doc_block(a.get("name"), a.get("text")))
            elif a.get("kind") == "image":
                try:
                    with open(os.path.join(AI_FS_DIR, a["path"]), "rb") as fh:
                        images.append({"mime": a.get("mime", "image/png"), "data": fh.read()})
                except OSError:
                    pass

        ai_on = bool(app.config.get("GEMINI_API_KEY") or app.config.get("ANTHROPIC_API_KEY"))
        answer, live = generate_reply(
            app.config, current_user.name.split(" ")[0], history, message,
            doc_blocks=doc_blocks, images=images, mode=mode,
            note_text=note_text, subject_name=subject_name, ai_on=ai_on,
        )

        if not user_msg:
            user_msg = AIConvMessage(
                conversation_id=conv.id, role="user", content=message,
                attachments_json=json.dumps(attachments) if attachments else None,
            )
            db.session.add(user_msg)
            db.session.flush()
        ai_msg = AIConvMessage(conversation_id=conv.id, role="assistant", content=answer)
        db.session.add(ai_msg)
        conv.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({
            "conversation": _ai_conv_dict(conv),
            "user_message": _ai_msg_dict(user_msg),
            "assistant_message": _ai_msg_dict(ai_msg),
            "live": live,
        })

    # ---- Assignments -----------------------------------------------
    @app.route("/assignments/<int:assignment_id>/submit", methods=["POST"])
    @student_required
    def assignment_submit(assignment_id):
        assignment = Assignment.query.get_or_404(assignment_id)
        subject = Subject.query.filter_by(id=assignment.subject_id, section_id=current_user.section_id).first_or_404()

        file = request.files.get("submission_file")
        if not file or file.filename == "":
            flash("Choose a file to submit.", "error")
            return redirect(url_for("subject_notes", subject_id=subject.id))

        filename = secure_filename(file.filename)
        unique_name = f"sub_{assignment.id}_{current_user.id}_{int(datetime.utcnow().timestamp())}_{filename}"
        filepath = os.path.join(Config.UPLOAD_FOLDER, "assignments", unique_name)
        file.save(filepath)

        existing = AssignmentSubmission.query.filter_by(assignment_id=assignment.id, student_id=current_user.id).first()
        if existing:
            existing.file_path = filepath
            existing.file_name = filename
            existing.submitted_at = datetime.utcnow()
            existing.marks = None
            existing.feedback = None
        else:
            db.session.add(AssignmentSubmission(
                assignment_id=assignment.id, student_id=current_user.id,
                file_path=filepath, file_name=filename,
            ))
        db.session.commit()
        flash("Assignment submitted.", "success")
        return redirect(url_for("subject_notes", subject_id=subject.id))

    # ---- Quizzes -----------------------------------------------
    @app.route("/quiz/<int:quiz_id>")
    @student_required
    def quiz_take(quiz_id):
        quiz = Quiz.query.get_or_404(quiz_id)
        subject = Subject.query.filter_by(id=quiz.subject_id, section_id=current_user.section_id).first_or_404()
        return render_template("quiz.html", quiz=quiz)

    @app.route("/quiz/<int:quiz_id>/submit", methods=["POST"])
    @student_required
    def quiz_submit(quiz_id):
        quiz = Quiz.query.get_or_404(quiz_id)
        Subject.query.filter_by(id=quiz.subject_id, section_id=current_user.section_id).first_or_404()

        questions_by_id = {q.id: q for q in quiz.questions}
        answers = {}
        for qid in questions_by_id:
            chosen = request.form.get(f"q{qid}")
            if chosen:
                answers[str(qid)] = chosen

        score, total, percent = score_quiz(answers, questions_by_id)
        label = result_label(
            percent, pass_threshold=Config.QUIZ_PASS_THRESHOLD, partial_threshold=Config.QUIZ_PARTIAL_THRESHOLD,
        )

        attempt = QuizAttempt(
            quiz_id=quiz.id, student_id=current_user.id,
            score=score, total=total, percent=percent, result_label=label,
        )
        db.session.add(attempt)
        db.session.commit()
        return redirect(url_for("quiz_result", quiz_id=quiz.id, attempt_id=attempt.id))

    @app.route("/quiz/<int:quiz_id>/result/<int:attempt_id>")
    @student_required
    def quiz_result(quiz_id, attempt_id):
        quiz = Quiz.query.get_or_404(quiz_id)
        Subject.query.filter_by(id=quiz.subject_id, section_id=current_user.section_id).first_or_404()
        attempt = QuizAttempt.query.filter_by(id=attempt_id, quiz_id=quiz.id, student_id=current_user.id).first_or_404()
        return render_template("quiz_result.html", quiz=quiz, attempt=attempt)

    # ---- Performance analytics -----------------------------------------
    @app.route("/analytics")
    @student_required
    def analytics():
        subjects = _student_subjects()
        subject_perf = []
        for s in subjects:
            attempts = QuizAttempt.query.join(Quiz).filter(
                Quiz.subject_id == s.id, QuizAttempt.student_id == current_user.id
            ).order_by(QuizAttempt.taken_at).all()
            avg = round(sum(a.percent for a in attempts) / len(attempts), 1) if attempts else None
            subject_perf.append({
                "subject": s, "avg_percent": avg, "attempts": len(attempts),
                "scores": [round(a.percent) for a in attempts],
            })
        weak = _weak_subjects(subjects)
        weekly_minutes = _weekly_study_minutes()
        return render_template(
            "analytics.html", subject_perf=subject_perf, weak_subjects=weak,
            weekly_minutes=weekly_minutes, study_streak=_study_streak(),
        )

    # ---- Notifications -----------------------------------------------
    @app.route("/notifications")
    def notifications():
        if not (is_student() or is_teacher()):
            flash("Please log in to continue.", "error")
            return redirect(url_for("student_login"))
        role = "student" if is_student() else "teacher"
        rows = Notification.query.filter_by(recipient_type=role, recipient_id=current_user.id).order_by(
            Notification.created_at.desc()
        ).limit(50).all()
        unread_ids = [n.id for n in rows if not n.is_read]
        if unread_ids:
            Notification.query.filter(Notification.id.in_(unread_ids)).update(
                {"is_read": True}, synchronize_session=False
            )
            db.session.commit()
        return render_template("notifications.html", notifications=rows)

    # ------------------------------------------------------------------
    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", code=500, message="Something went wrong."), 500

    return app


app = create_app()


def _bootstrap_database():
    """Create tables, and a first admin if ADMIN_USERNAME / ADMIN_PASSWORD are set.
    Runs under gunicorn too, where the __main__ block below never executes."""
    with app.app_context():
        db.create_all()
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")
        if username and password and not Admin.query.filter_by(username=username).first():
            admin = Admin(name=os.environ.get("ADMIN_NAME", "Administrator"), username=username)
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()


_bootstrap_database()

if __name__ == "__main__":
    app.run(debug=True)
