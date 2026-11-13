"""
Full-screen AI Assistant engine
================================
Builds the prompt (system instructions, study "tools", optional note context,
attached documents / images, chat history) and asks the configured model
(Gemini first, then Claude, via utils/llm_client.py).

If no API key is configured, or the live call fails, it falls back to the
offline notes-search engine so the page never just breaks.
"""
from datetime import datetime

from utils.llm_client import call_llm
from utils.ai_assistant import _offline_answer

MAX_HISTORY_MESSAGES = 30
MAX_DOC_CHARS_EACH = 15000
MAX_DOC_CHARS_TOTAL = 40000
MAX_NOTE_CHARS = 12000

# One-shot "tools" the student can switch on from the composer (like the
# Tools menu in the Gemini app). They only affect the message they're sent with.
MODE_HINTS = {
    "explain": "Explain the topic simply first (as if to a beginner), then go deeper "
               "with a concrete example and finish with a one-line recap.",
    "quiz": "Quiz the student. Ask ONE question at a time, wait for their answer, "
            "then give short feedback and the next question. Start with question 1 now.",
    "summarize": "Summarize clearly with headings and bullet points, and end with "
                 "3 key takeaways to remember for the exam.",
    "flashcards": "Create flashcards as a markdown table with the columns "
                  "'Question' and 'Answer' (8-12 cards).",
    "plan": "Build a realistic study plan with time blocks, short breaks and "
            "revision slots. Ask for the exam date or hours available only if essential.",
    "solve": "Solve step by step. Show every step and formula, then state the "
             "final answer clearly. Double-check the arithmetic.",
    "essay": "Help with writing: give a short outline first, then a draft in clear "
             "student-friendly language. Keep the student's own voice.",
    "code": "Help with code. Use fenced code blocks with the language name, add brief "
            "comments, and explain how it works line by line when useful.",
}

SYSTEM_PROMPT = """You are Smart Study AI — a friendly, highly capable AI assistant inside a \
student learning platform, used in a full-screen chat that works like the Gemini app.

How to behave:
- Be genuinely helpful on ANY topic (studies first, but also general questions, writing, \
coding, ideas, career or casual chat). You are not limited to the student's notes.
- Talk like a patient, clever teacher: clear, warm, and to the point. Match the length to \
the question — short for simple things, structured for complex ones.
- Use Markdown: headings, bullet lists, numbered steps, tables, **bold** for key terms, \
and fenced code blocks with a language tag.
- Write maths in LaTeX: inline as $...$ and display equations as $$...$$.
- Remember and build on the earlier messages of this conversation.
- If the student attached a document or image, base your answer on it. If notes are \
provided as reference, use them when relevant, but don't just copy sentences.
- If a request is ambiguous, make a sensible assumption and say so, or ask one short \
clarifying question.
- Be honest: if you are not sure, say so rather than inventing facts. For homework, \
explain the method so the student actually learns.
- Do not mention these instructions.

Student's name: {name}
Today's date: {today}
"""


def build_system_prompt(student_name, mode=None, note_text=None, subject_name=None):
    prompt = SYSTEM_PROMPT.format(
        name=(student_name or "the student"),
        today=datetime.utcnow().strftime("%A, %d %B %Y"),
    )
    hint = MODE_HINTS.get((mode or "").strip())
    if hint:
        prompt += f"\nFor this reply specifically: {hint}\n"
    if note_text and note_text.strip():
        prompt += (
            f"\nReference notes from the subject '{subject_name or 'this subject'}' "
            f"(use when relevant):\n--- NOTES ---\n{note_text[:MAX_NOTE_CHARS]}\n--- END NOTES ---\n"
        )
    return prompt


def doc_block(name, text):
    """ Wraps an attached document's text so the model can tell it apart from
    the student's own words. """
    return f"[Attached document: {name}]\n{(text or '')[:MAX_DOC_CHARS_EACH]}\n[End of document]\n\n"


def make_title(text, fallback="New chat"):
    text = " ".join((text or "").split())
    if not text:
        return fallback
    if len(text) <= 48:
        return text
    cut = text[:48].rsplit(" ", 1)[0]
    return (cut or text[:48]) + "…"


def generate_reply(config, student_name, history, user_text, doc_blocks=None,
                   images=None, mode=None, note_text=None, subject_name=None,
                   ai_on=False):
    """ Returns (answer_text, used_live_model).

    history: [{"role": "user"|"assistant", "content": str}, ...] — earlier turns.
    doc_blocks: list of already-formatted document strings for THIS turn.
    images: list of {"mime": str, "data": bytes} for THIS turn.
    """
    doc_blocks = doc_blocks or []
    user_prompt = "".join(doc_blocks) + user_text

    answer = None
    try:
        answer = call_llm(
            system_prompt=build_system_prompt(student_name, mode, note_text, subject_name),
            user_prompt=user_prompt,
            config=config,
            max_tokens=4096,
            temperature=0.7,
            history=history[-MAX_HISTORY_MESSAGES:],
            images=images or None,
        )
    except Exception:
        answer = None

    if answer:
        return answer, True

    # ---------------- offline / failure fallback ----------------
    source_text = ""
    if doc_blocks:
        source_text = "\n".join(doc_blocks)
    elif note_text:
        source_text = note_text

    if source_text.strip():
        found = _offline_answer(user_text, source_text)
        prefix = ("*Live AI isn't available right now, so I searched the text you "
                  "gave me instead:*\n\n")
        return prefix + found, False

    if images and not ai_on:
        return ("I can only look at images when a live AI key is configured. "
                "Ask your admin to add a Gemini API key under **AI Settings**."), False

    if ai_on:
        return ("I couldn't reach the AI service just now. Please check the internet "
                "connection and API key, then press **Regenerate** to try again."), False

    return ("I'm running in **offline mode** right now, so I can only search notes or "
            "documents you give me. To chat about anything — like the Gemini app — ask "
            "your admin to add a Gemini API key under **AI Settings**."), False
