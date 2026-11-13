"""
AI Assistant
=============
Powers two features: "Summarize this topic" and "Ask a question" (the
ChatGPT-style Q&A). Both work out of the box with zero setup using a
built-in offline engine — frequency-based extractive summarization and
keyword-overlap retrieval for Q&A, the same spirit as utils/quiz_generator.py.

Optional upgrade: set GEMINI_API_KEY or ANTHROPIC_API_KEY (see config.py)
and both functions transparently switch to calling a real LLM (via
utils/llm_client.py) for noticeably better, more natural answers. If the
call fails for any reason (no key, no package installed, network error,
rate limit), it falls back to the offline engine automatically — the
feature never just breaks.
"""

import re
from collections import Counter

from utils.llm_client import call_llm as _call_llm

STOPWORDS = set("""
a an the is are was were be been being of in on at to for with and or but
this that these those it its as by from into over under again further then
once here there when where why how all any both each few more most other
some such no nor not only own same so than too very can will just should
now also we you they he she i his her their our your them us if have has
had do does did doing about above below up down out off than which who
whom what when while because until unless though although across slide
""".split())


# -------------------------------------------------------------------------
# Shared text helpers
# -------------------------------------------------------------------------
def _split_sentences(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if len(s.split()) >= 4]


def _keywords(text):
    words = re.findall(r"[A-Za-z][A-Za-z\-']{2,}", text.lower())
    return [w for w in words if w not in STOPWORDS]


_CODE_HINTS = re.compile(
    r"(def |import |print\(|#\s*\w|==|!=|\{.*\}|\[.*\]|\bf['\"]|_\w+\s*=|"
    r"\breturn\b|\bfor \w+ in\b|\bif \w+.*:)"
)


def _looks_like_code(sentence):
    """ Lab manuals / technical notes often contain literal code snippets.
    Those make poor summary/answer sentences even though they score high on
    keyword density, so they're penalised rather than picked. """
    if _CODE_HINTS.search(sentence):
        return True
    symbol_count = len(re.findall(r"[{}()\[\]=_#]", sentence))
    return symbol_count >= 4


# -------------------------------------------------------------------------
# Offline extractive summarizer
# -------------------------------------------------------------------------
def _offline_summarize(text, max_sentences=6):
    sentences = _split_sentences(text)
    if not sentences:
        return "There isn't enough readable text in this document to summarize yet."
    if len(sentences) <= max_sentences:
        return " ".join(sentences)

    freq = Counter()
    for s in sentences:
        freq.update(_keywords(s))
    if freq:
        top_val = max(freq.values())
        for w in freq:
            freq[w] = freq[w] / top_val

    scored = []
    for idx, s in enumerate(sentences):
        kws = _keywords(s)
        score = sum(freq.get(w, 0) for w in kws) / (len(kws) + 1)
        # slight boost for early sentences (often topic-setting) and for
        # sentences immediately following a "Slide N:" marker
        if idx < 3:
            score += 0.15
        if _looks_like_code(s):
            score -= 1.0
        scored.append((score, idx, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = sorted(scored[:max_sentences], key=lambda x: x[1])  # restore original order
    return " ".join(s for _, _, s in chosen)


def summarize_text(text, max_sentences=6, config=None, history=None):
    """ Returns a plain-text summary. Tries the real LLM first (if
    configured), otherwise uses the offline extractive engine. """
    if not text or not text.strip():
        return "Upload a document with readable text first — this one appears to be empty or unreadable."

    llm_result = _call_llm(
        system_prompt=(
            "You are a calm, encouraging study assistant, chatting with a student in "
            "real time. Summarize the student's uploaded notes clearly in 5-8 sentences, "
            "focusing on the key concepts and definitions a student would need to "
            "remember for an exam. Use plain language, no markdown headers."
        ),
        user_prompt=f"Summarize these notes:\n\n{text[:12000]}",
        config=config,
        max_tokens=600,
        history=history,
    )
    if llm_result:
        return llm_result
    return _offline_summarize(text, max_sentences=max_sentences)


# -------------------------------------------------------------------------
# Offline retrieval-based Q&A
# -------------------------------------------------------------------------
def _offline_answer(question, text, max_sentences=3):
    sentences = _split_sentences(text)
    if not sentences:
        return ("I don't have any readable notes to search yet — upload a document "
                "for this subject first.")

    q_kws = set(_keywords(question))
    if not q_kws:
        return "Could you rephrase that as a more specific question about your notes?"

    scored = []
    for idx, s in enumerate(sentences):
        s_kws = set(_keywords(s))
        overlap = len(q_kws & s_kws)
        if overlap == 0:
            continue
        score = overlap / (len(q_kws) ** 0.5)
        if _looks_like_code(s):
            score -= 1.5
        scored.append((score, idx, s))

    if not scored:
        return ("I couldn't find anything about that in the notes you've uploaded for "
                "this subject. Try rephrasing, or upload notes that cover this topic.")

    scored.sort(key=lambda x: x[0], reverse=True)
    top = sorted(scored[:max_sentences], key=lambda x: x[1])
    answer = " ".join(s for _, _, s in top)
    return answer


def answer_question(question, text, subject_name=None, config=None, history=None):
    """ Returns a real, live conversational AI answer (Gemini/Claude, whichever
    key is configured) — a genuine back-and-forth chat, not just a lookup that
    echoes back sentences from the uploaded notes. Notes are passed along as
    *optional supporting context* the model may draw on, but the model is
    always free to answer from its own knowledge and to use the ongoing
    conversation history, exactly like talking to Gemini directly. Only if no
    API key is configured (or the live call fails) does this drop back to the
    offline, notes-only keyword engine as a safety net. """

    has_notes = bool(text and text.strip())

    if has_notes:
        context_block = (
            f"The student has notes uploaded for {subject_name or 'this subject'}. "
            f"Use them as optional supporting context — pull in a relevant fact or "
            f"definition when it helps, but never just copy-paste sentences from "
            f"them, and don't limit yourself to only what's written here if the "
            f"question goes beyond it.\n\n--- NOTES (reference only) ---\n"
            f"{text[:12000]}\n--- END NOTES ---\n\n"
        )
    else:
        context_block = (
            "The student hasn't uploaded any notes for this context yet. That's "
            "fine — just answer the question yourself from your own knowledge, "
            "the way a normal AI chat assistant would.\n\n"
        )

    llm_result = _call_llm(
        system_prompt=(
            "You are a friendly, knowledgeable AI study assistant, having a real-time, "
            "back-and-forth chat with a student — the same kind of experience as "
            "chatting directly with Gemini or ChatGPT, not a document search tool. "
            "Remember and build on what was said earlier in this conversation "
            "(it's provided as chat history) and refer back to it naturally, e.g. "
            "'like I mentioned earlier' when relevant. Explain concepts in your own "
            "words like a patient teacher, use examples where they help, and keep "
            "things conversational rather than a wall of text. You are never "
            "restricted to only the student's uploaded notes — you can and should "
            "answer general questions, casual messages, and follow-ups too. Keep "
            "answers reasonably concise and exam-relevant when the topic is academic."
        ),
        user_prompt=f"{context_block}Student: {question}",
        config=config,
        max_tokens=2048,
        history=history,
    )

    if llm_result:
        return llm_result

    # --- Offline fallback (no API key configured, or the live call failed) ---
    if not has_notes:
        subj = f" for {subject_name}" if subject_name else ""
        return (
            f"I'm running on the offline engine right now, so I can only search notes "
            f"you've uploaded{subj} — and there aren't any yet. Add a GEMINI_API_KEY "
            f"in your .env file for real conversational answers, or upload study "
            f"material to search."
        )
    return _offline_answer(question, text)