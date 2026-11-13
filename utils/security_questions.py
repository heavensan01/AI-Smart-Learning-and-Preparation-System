"""
Security questions used by the "Forgot password" feature.

Students choose a question + answer while registering. Teachers get theirs
when the admin creates the account (and can change it later from
Account > Change Password). Answers are never stored in plain text: they are
normalised (trimmed, lower-cased, extra spaces collapsed) and then hashed
exactly like a password, so "  Tommy " and "tommy" are treated as the same.
"""
import re

from werkzeug.security import generate_password_hash, check_password_hash

SECURITY_QUESTIONS = [
    "What was the name of your first pet?",
    "What is your mother's maiden name?",
    "In which city were you born?",
    "What was the name of your primary school?",
    "Who was your favourite teacher?",
    "What was your childhood nickname?",
    "What is the name of your best childhood friend?",
    "What is your favourite food?",
    "What is your favourite movie?",
    "What was the make of your first mobile phone?",
]

MIN_ANSWER_LENGTH = 2


def normalize_answer(raw):
    """Lower-case, trim and collapse internal whitespace."""
    return re.sub(r"\s+", " ", (raw or "").strip().lower())


def is_valid_question(question):
    return question in SECURITY_QUESTIONS


def hash_answer(raw):
    return generate_password_hash(normalize_answer(raw))


def check_answer(stored_hash, raw):
    if not stored_hash:
        return False
    return check_password_hash(stored_hash, normalize_answer(raw))


def validate_question_and_answer(question, answer):
    """Returns an error message string, or None if both are acceptable."""
    if not is_valid_question(question):
        return "Please choose a security question from the list."
    if len(normalize_answer(answer)) < MIN_ANSWER_LENGTH:
        return "Please enter an answer to your security question."
    return None
