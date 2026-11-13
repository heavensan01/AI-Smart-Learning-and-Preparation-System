"""
AI Quiz Generator (v3)
=======================
Two quiz-generation paths:

  1. LLM-GENERATED (preferred, when GEMINI_API_KEY or ANTHROPIC_API_KEY is
     configured) — asks the model to write genuine, well-formed multiple
     choice questions that test understanding of the material (definitions,
     "why", "how", applied/scenario questions), not just fill-in-the-blank.
     The model returns strict JSON, which is parsed and validated; any
     malformed or incomplete response falls back to the offline path below
     rather than failing the request.

  2. OFFLINE (default, zero setup) — fully self-contained, no API key
     required, so the project runs immediately. Two question styles:

     - DEFINITION MATCH — for sentences shaped like "<Term> is/are/refers
       to <definition>", the term is hidden and the student matches the
       definition to the right term.
     - KEY-TERM BLANK — for other informative sentences, the most
       important *term* is blanked out (never the first word of the
       sentence, preferring multi-word technical phrases).

     Distractors are pulled from other genuine terms found in the same
     document, matched roughly by word-count and length, so wrong options
     stay topic-relevant instead of being randomly chosen words.

"Studied or not" decision lives in result_label() and is unchanged.
"""

import json
import random
import re
import sys
from collections import Counter

from docx import Document

from utils.llm_client import call_llm
from utils.document_reader import text_to_paragraphs

STOPWORDS = set("""
a an the is are was were be been being of in on at to for with and or but
this that these those it its as by from into over under again further then
once here there when where why how all any both each few more most other
some such no nor not only own same so than too very can will just should
now also we you they he she i his her their our your them us if have has
had do does did doing about above below up down out off than which who
whom what when while because until unless though although across
""".split())

LINK_WORDS = {"of", "as", "the", "and", "or", "in", "on", "for", "a", "an", "to", "with"}
DEFINING_VERBS = ("is", "are", "refers to", "means", "denotes", "represents", "consists of")
FILLER_WORDS = {"allowing", "using", "providing", "enabling", "including", "according",
                 "resulting", "following", "between", "through", "within", "without"}

FILLER_OPTIONS = ["None of these", "All of the above", "Cannot be determined from the notes"]


# ---------------------------------------------------------------- helpers --

def _clean_text(raw):
    raw = raw.replace("\xa0", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def extract_text_from_docx(filepath):
    doc = Document(filepath)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    return paragraphs


def _split_sentences(paragraphs):
    text = _clean_text(" ".join(paragraphs))
    sentences = re.split(r"(?<=[.!?])\s+", text)
    cleaned, seen = [], set()
    for s in sentences:
        s = s.strip()
        word_count = len(s.split())
        if 7 <= word_count <= 32 and s.lower() not in seen:
            cleaned.append(s)
            seen.add(s.lower())
    return cleaned


def _word(token):
    return re.sub(r"^[^\w\-]+|[^\w\-]+$", "", token)


def _extract_phrases(sentence):
    """
    Pull out technical-sounding terms: either a multi-word Title Case
    phrase ("Platform as a Service") or a single capitalised / long
    technical word ("Virtualization"). Returns list of (phrase, start_idx)
    so callers can tell whether a phrase opens the sentence.
    """
    words = sentence.split()
    phrases = []
    i, n = 0, len(words)
    while i < n:
        w = _word(words[i])
        if w and w[0].isupper() and len(w) > 2:
            phrase_words, j = [w], i + 1
            while j < n:
                wj = _word(words[j])
                if not wj:
                    break
                if wj[0].isupper() and len(wj) > 1:
                    phrase_words.append(wj)
                    j += 1
                elif wj.lower() in LINK_WORDS and j + 1 < n:
                    nxt = _word(words[j + 1])
                    if nxt and nxt[0].isupper():
                        phrase_words.extend([wj.lower(), nxt])
                        j += 2
                    else:
                        break
                else:
                    break
            if 1 <= len(phrase_words) <= 5:
                phrases.append((" ".join(phrase_words), i))
            i = j if j > i else i + 1
        else:
            i += 1

    # also collect plain long technical words (lowercase ok), for sentences
    # with no capitalised terms at all
    if not phrases:
        for idx, raw in enumerate(words):
            w = _word(raw)
            if len(w) >= 6 and w.lower() not in STOPWORDS:
                phrases.append((w, idx))
    return phrases


def _blank_out(sentence, term):
    pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
    if not pattern.search(sentence):
        return None
    return pattern.sub("______", sentence, count=1)


def _pick_distractors(answer, pool, used, max_count=3):
    answer_words = answer.split()
    candidates = [
        p for p in pool
        if p.lower() != answer.lower()
        and p.lower() not in used
        and abs(len(p.split()) - len(answer_words)) <= 1
        and abs(len(p) - len(answer)) <= max(6, len(answer) // 2)
    ]
    random.shuffle(candidates)
    chosen = candidates[:max_count]

    # widen the net if the document was short / repetitive
    if len(chosen) < max_count:
        wider = [p for p in pool if p.lower() != answer.lower() and p.lower() not in used and p not in chosen]
        random.shuffle(wider)
        chosen += wider[: max_count - len(chosen)]

    i = 0
    while len(chosen) < max_count and i < len(FILLER_OPTIONS):
        if FILLER_OPTIONS[i] not in chosen:
            chosen.append(FILLER_OPTIONS[i])
        i += 1
    return chosen[:max_count]


def _build_mcq(question_text, answer, pool, used_answers):
    distractors = _pick_distractors(answer, pool, used_answers)
    options = distractors + [answer]
    random.shuffle(options)
    correct_letter = "ABCD"[options.index(answer)]
    return {
        "question_text": question_text,
        "option_a": options[0],
        "option_b": options[1],
        "option_c": options[2],
        "option_d": options[3],
        "correct_option": correct_letter,
    }


# --------------------------------------------------------- main generator --

def generate_questions_from_text(paragraphs, num_questions=10):
    sentences = _split_sentences(paragraphs)
    if not sentences:
        return []

    # Build a global term pool across the document (for distractors) and
    # tag each sentence with its candidate terms.
    sentence_terms = [_extract_phrases(s) for s in sentences]
    term_pool = Counter()
    for terms in sentence_terms:
        for term, _ in terms:
            term_pool[term] += 1
    pool = [t for t in term_pool.keys() if len(t) > 2]

    used_answers = set()
    definition_qs, blank_qs = [], []

    def_re = re.compile(
        r"^(?P<term>[A-Z][\w\-]+(?:\s+(?:" + "|".join(LINK_WORDS) + r")\s+[A-Z]?[\w\-]+|\s+[A-Z][\w\-]+){0,3})"
        r"\s+(?:" + "|".join(DEFINING_VERBS) + r")\s+(?P<rest>.{10,160})$"
    )

    order = list(range(len(sentences)))
    random.shuffle(order)

    for idx in order:
        sentence = sentences[idx]
        terms = sentence_terms[idx]
        if not terms:
            continue

        # ---- Style 1: definition-match question ----
        m = def_re.match(sentence)
        if m:
            term = m.group("term").strip()
            rest = m.group("rest").strip().rstrip(".")
            if term.lower() not in used_answers and 2 <= len(term) <= 60:
                question_text = f'Which term is being described below?\n"{rest[0].upper() + rest[1:]}."'
                definition_qs.append(_build_mcq(question_text, term, pool, used_answers))
                used_answers.add(term.lower())
                continue

        # ---- Style 2: key-term blank (never the very first word) ----
        candidates = [t for t, start in terms if start > 0 and t.lower() not in used_answers]
        if not candidates:
            candidates = [t for t, _ in terms if t.lower() not in used_answers]
        if not candidates:
            continue
        # prefer the longest / most specific candidate term
        answer = max(candidates, key=len)
        blanked = _blank_out(sentence, answer)
        if not blanked:
            continue
        question_text = f"Fill in the blank: {blanked}"
        blank_qs.append(_build_mcq(question_text, answer, pool, used_answers))
        used_answers.add(answer.lower())

    # Prioritise definition-style questions (read more naturally), then
    # top up with key-term blanks until we hit the target count.
    questions = definition_qs + blank_qs
    return questions[:num_questions]


def _strip_code_fences(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _repair_json_punctuation(text):
    """ Cheap, safe repairs for the most common ways models mangle JSON:
    trailing commas before a closing bracket, and smart/curly quotes that
    break strict json.loads parsing. """
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = re.sub(r",\s*([\]}])", r"\1", text)
    return text


def _salvage_question_objects(text):
    """ Last-resort recovery when the array as a whole won't parse (e.g.
    the response got cut off mid-array because it hit max_output_tokens).
    Scans for individual {...} objects that look like question entries and
    parses each independently, keeping whichever ones are valid instead of
    discarding the entire batch over one broken/truncated entry. """
    objects = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    objects.append(text[start:i + 1])
                    start = None
    parsed = []
    for obj_text in objects:
        try:
            obj = json.loads(_repair_json_punctuation(obj_text))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "question" in obj:
            parsed.append(obj)
    return parsed


def _extract_json_array(raw_text):
    """ Models sometimes wrap JSON in ```json fences, add a sentence
    before/after it, leave a trailing comma, use smart quotes, or get cut
    off mid-array. Try progressively more forgiving strategies rather than
    giving up (and silently dropping to the offline fill-in-the-blank
    generator) the moment strict parsing fails once. """
    if not raw_text:
        return None
    text = _strip_code_fences(raw_text)
    start = text.find("[")
    end = text.rfind("]")

    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_repair_json_punctuation(candidate))
        except json.JSONDecodeError:
            pass

    # Array brackets missing, malformed, or the response was truncated
    # before the closing "]" — salvage whatever individual question
    # objects are still intact.
    salvaged = _salvage_question_objects(text)
    if salvaged:
        print(
            f"[Quiz Generator] Salvaged {len(salvaged)} question object(s) "
            "from a malformed/truncated Gemini response.",
            file=sys.stderr,
        )
        return salvaged

    print(
        "[Quiz Generator] Could not parse any valid JSON from the AI "
        f"response. First 300 chars: {text[:300]!r}",
        file=sys.stderr,
    )
    return None


def _validate_llm_questions(raw_questions):
    """ Normalises and sanity-checks the model's output into our internal
    question dict shape, silently dropping any malformed entries rather
    than failing the whole batch. """
    valid = []
    for q in raw_questions or []:
        if not isinstance(q, dict):
            continue
        question_text = str(q.get("question", "")).strip()
        options = q.get("options")
        correct = str(q.get("correct_option", "")).strip().upper()

        if not question_text or not isinstance(options, dict) or correct not in ("A", "B", "C", "D"):
            continue
        opts = {k: str(options.get(k, "")).strip() for k in ("A", "B", "C", "D")}
        if not all(opts.values()):
            continue

        valid.append({
            "question_text": question_text,
            "option_a": opts["A"], "option_b": opts["B"],
            "option_c": opts["C"], "option_d": opts["D"],
            "correct_option": correct,
        })
    return valid


_QUIZ_SYSTEM_PROMPT = (
    "You are an expert exam question writer helping a student test their "
    "understanding of their own study material (it may be lecture notes, a "
    "textbook chapter, or an assignment PDF). Read the material, figure out "
    "the key concepts, and WRITE YOUR OWN multiple-choice questions that "
    "test whether the student understands those concepts.\n\n"
    "Strict rules:\n"
    "- Never quote or lightly reword a single sentence from the material "
    "and turn it into a fill-in-the-blank question. Every question must be "
    "an original question you composed, not a copied sentence with a word "
    "removed.\n"
    "- Mix question types: definitions/terminology, conceptual 'why' or "
    "'how' questions, comparisons, and applied/scenario questions that use "
    "the concept in a new situation.\n"
    "- Each question has exactly one correct answer and three plausible, "
    "topic-relevant wrong answers. No joke options, no 'all of the above', "
    "no 'none of the above'.\n"
    "- Keep each question and option on a single line of text (no literal "
    "newlines inside a string).\n\n"
    "Respond with ONLY a single JSON array, no commentary, no markdown "
    "fences, no text before or after it, in exactly this shape:\n"
    '[{"question": "...", "options": {"A": "...", "B": "...", "C": "...", "D": "..."}, '
    '"correct_option": "A"}]'
)

_QUIZ_RETRY_SUFFIX = (
    "\n\nIMPORTANT: your previous reply could not be parsed as valid JSON. "
    "Reply again with ONLY the JSON array — no markdown fences, no leading "
    "or trailing text, no trailing commas, and make sure every bracket is "
    "closed."
)


def generate_quiz_with_llm(text, num_questions=10, config=None, attempts=2):
    """ Asks a real LLM (Gemini, then Anthropic — see utils/llm_client.py)
    to write genuine quiz questions from the given notes. Returns a list of
    question dicts in the same shape as the offline generator, or an empty
    list if no LLM is configured or the response couldn't be parsed after
    retrying. """
    if not text or not text.strip():
        return []

    user_prompt = (
        f"Write {num_questions} multiple-choice questions from this material:\n\n{text[:12000]}"
    )

    questions = []
    for attempt in range(1, max(1, attempts) + 1):
        prompt = user_prompt if attempt == 1 else user_prompt + _QUIZ_RETRY_SUFFIX
        # Slightly larger token budget than the estimated need, so a full
        # batch of 10 questions doesn't get cut off mid-JSON (a truncated
        # response is the #1 cause of "valid-looking AI reply, invalid
        # JSON" failures that used to fall straight to the offline engine).
        raw = call_llm(
            _QUIZ_SYSTEM_PROMPT, prompt, config,
            max_tokens=3000, temperature=0.4,
        )
        if raw is None:
            # No provider configured / call failed outright — retrying
            # won't help, stop here and let the caller fall back offline.
            break
        parsed = _extract_json_array(raw)
        questions = _validate_llm_questions(parsed)
        if len(questions) >= max(3, num_questions // 2):
            break

    return questions[:num_questions]


def generate_smart_quiz(text, num_questions=10, config=None):
    """ Top-level entry point used by the app: tries the LLM quiz writer
    first (if configured), and falls back to the offline generator if no
    key is set, the call fails, or too few valid questions come back. """
    questions, _source = generate_smart_quiz_with_source(text, num_questions=num_questions, config=config)
    return questions


def generate_smart_quiz_with_source(text, num_questions=10, config=None):
    """ Same as generate_smart_quiz, but also reports which engine actually
    produced the questions ("ai", "ai+offline", or "offline") so the caller
    can show the student what happened instead of a silent fallback. """
    llm_questions = generate_quiz_with_llm(text, num_questions=num_questions, config=config)
    if len(llm_questions) >= max(3, num_questions // 2):
        return llm_questions[:num_questions], "ai"

    offline_questions = generate_questions_from_text(text_to_paragraphs(text), num_questions=num_questions)
    # If the LLM produced a few good questions but not enough, top up with
    # offline ones rather than throwing away what it got right.
    if llm_questions:
        seen = {q["question_text"] for q in llm_questions}
        for q in offline_questions:
            if len(llm_questions) >= num_questions:
                break
            if q["question_text"] not in seen:
                llm_questions.append(q)
        return llm_questions, "ai+offline"
    return offline_questions, "offline"


def build_quiz_from_docx(filepath, num_questions=10):
    paragraphs = extract_text_from_docx(filepath)
    if not paragraphs:
        return []
    return generate_questions_from_text(paragraphs, num_questions=num_questions)


def score_quiz(answers, questions_by_id):
    """
    answers: dict {question_id(str): "A"/"B"/"C"/"D"}
    questions_by_id: dict {question_id(int): QuizQuestion}
    returns (score, total, percent)
    """
    total = len(questions_by_id)
    score = 0
    for qid, question in questions_by_id.items():
        chosen = answers.get(str(qid))
        if chosen and chosen.upper() == (question.correct_option or "").upper():
            score += 1
    percent = round((score / total) * 100, 1) if total else 0
    return score, total, percent


def result_label(percent, pass_threshold=70, partial_threshold=40):
    if percent >= pass_threshold:
        return "Studied Well"
    elif percent >= partial_threshold:
        return "Needs Revision"
    return "Not Studied Properly"
