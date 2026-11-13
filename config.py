import os
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Loads a .env file in the project root if one exists (see .env.example).
# This is the easiest way to set GEMINI_API_KEY / ANTHROPIC_API_KEY without
# needing to know shell environment-variable syntax — just copy
# .env.example to .env, paste your key in, and restart the app.
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "smart-study-secret-key-change-me")

    # ---------------------------------------------------------------
    # DATABASE
    # By default the app runs on SQLite so it works instantly with
    # zero setup (great for demos / evaluation). To use MySQL instead
    # (as per the project's official tech stack), set USE_MYSQL=1 and
    # fill in the MYSQL_* environment variables, or just edit the
    # values below. Tables are created automatically on startup.
    # ---------------------------------------------------------------
    USE_MYSQL = os.environ.get("USE_MYSQL", "0") == "1"

    MYSQL_USER = os.environ.get("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "password")
    MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
    MYSQL_PORT = os.environ.get("MYSQL_PORT", "3306")
    MYSQL_DB = os.environ.get("MYSQL_DB", "smart_study_system")

    if USE_MYSQL:
        SQLALCHEMY_DATABASE_URI = (
            f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:"
            f"{MYSQL_PORT}/{MYSQL_DB}"
        )
    else:
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(
            BASE_DIR, "instance", "smart_study.db"
        )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "notes")
    ALLOWED_NOTE_EXTENSIONS = {"docx", "pdf", "pptx", "txt"}
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

    # ---------------------------------------------------------------
    # AI ASSISTANT & AI QUIZ GENERATOR
    # By default, summarizing, Q&A, and quiz generation all run on a
    # built-in, fully offline engine — no API key, no cost, works
    # immediately. Setting either key below automatically upgrades every
    # AI feature to call a real model instead — nothing else needs to
    # change. If both are set, Gemini is tried first, then Anthropic, then
    # the offline engine (in that order, per request, with automatic
    # fallback if a call fails).
    # ---------------------------------------------------------------
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    AI_CHAT_HISTORY_LIMIT = 40   # messages kept per student in the floating widget / AI tab

    # Quiz scoring thresholds (percentage)
    QUIZ_PASS_THRESHOLD = 70      # "Studied Well"
    QUIZ_PARTIAL_THRESHOLD = 40   # "Needs Revision"  (below this => "Not Studied Properly")
