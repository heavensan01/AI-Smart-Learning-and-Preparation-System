"""
LLM Client
===========
Shared entry point for Gemini and Anthropic.
Falls back safely when a provider/model is unavailable.
"""

import sys

# Tried in order after whatever GEMINI_MODEL is configured, so the app keeps
# working even if one model name is retired/renamed on Google's side. Newest
# generation first, oldest last, since Google has been retiring older model
# names over the course of 2026.
_GEMINI_MODEL_FALLBACKS = [
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


def _cfg(config, key, default=None):
    """ Reads a setting from either a plain object (attribute access, e.g.
    the Config class) or a dict-like object (e.g. Flask's app.config,
    which is a dict subclass and does NOT support getattr — using
    getattr() directly on it always silently returns the default). """
    if config is None:
        return default
    if isinstance(config, dict) or hasattr(config, "get"):
        try:
            value = config.get(key, default)
            if value not in (None, ""):
                return value
        except TypeError:
            pass
    return getattr(config, key, default)


def _log_llm_error(provider, model, error):
    print(
        f"[AI Assistant] {provider} call failed "
        f"(model={model}): {type(error).__name__}: {error}",
        file=sys.stderr,
    )


def _to_gemini_history(history):
    """ Converts our internal [{role: 'user'|'assistant', content: str}, ...]
    into the {role: 'user'|'model', parts: [...]} shape the Gemini SDK's
    start_chat() expects, so previous turns are actually sent back to the
    model on every request instead of each message being answered in
    isolation. """
    turns = []
    for turn in history or []:
        role = "user" if turn.get("role") == "user" else "model"
        content = (turn.get("content") or "").strip()
        if content:
            turns.append({"role": role, "parts": [content]})
    return turns


def call_gemini(system_prompt, user_prompt, config,
                max_tokens=700, temperature=0.4, history=None, images=None):

    api_key = _cfg(config, "GEMINI_API_KEY", "")

    if not api_key:
        return None

    try:
        import google.generativeai as genai
    except ImportError as e:
        _log_llm_error("Gemini", "import", e)
        return None

    gemini_history = _to_gemini_history(history)

    try:
        genai.configure(api_key=api_key)

        configured_model = _cfg(
            config,
            "GEMINI_MODEL",
            "gemini-2.0-flash"
        )

        candidates = [configured_model]

        for model in _GEMINI_MODEL_FALLBACKS:
            if model not in candidates:
                candidates.append(model)

        for model_name in candidates:

            try:
                print(f"[AI Assistant] Trying Gemini model: {model_name}")

                model = genai.GenerativeModel(
                    model_name,
                    system_instruction=system_prompt
                )

                generation_config = {
                    "max_output_tokens": max_tokens,
                    "temperature": temperature,
                }

                # Real-time chat: replay prior turns via start_chat() so the
                # model actually remembers the conversation, instead of
                # treating every message as a brand-new, context-free call.
                # Optional images (full-screen AI assistant) are sent as extra
                # parts of the current turn: [text, {mime_type, data}, ...]
                payload = user_prompt
                if images:
                    payload = [user_prompt] + [
                        {"mime_type": img["mime"], "data": img["data"]}
                        for img in images
                    ]

                if gemini_history:
                    chat = model.start_chat(history=gemini_history)
                    response = chat.send_message(
                        payload, generation_config=generation_config
                    )
                else:
                    response = model.generate_content(
                        payload, generation_config=generation_config
                    )

                text = getattr(response, "text", "")

                if text and text.strip():
                    return text.strip()

            except Exception as e:
                _log_llm_error("Gemini", model_name, e)
                continue

    except Exception as e:
        _log_llm_error("Gemini", "configuration", e)

    return None


def call_anthropic(system_prompt, user_prompt,
                   config, max_tokens=700, history=None, images=None):

    api_key = _cfg(config, "ANTHROPIC_API_KEY", "")

    if not api_key:
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(
            api_key=api_key
        )

        model_name = _cfg(
            config,
            "ANTHROPIC_MODEL",
            "claude-sonnet-4-6"
        )

        # Real-time chat: replay prior turns so Claude remembers the
        # conversation instead of answering each message in isolation.
        messages = []
        for turn in history or []:
            role = "user" if turn.get("role") == "user" else "assistant"
            content = (turn.get("content") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
        if images:
            import base64
            blocks = [
                {"type": "image", "source": {
                    "type": "base64", "media_type": img["mime"],
                    "data": base64.b64encode(img["data"]).decode("ascii"),
                }}
                for img in images
            ]
            blocks.append({"type": "text", "text": user_prompt})
            messages.append({"role": "user", "content": blocks})
        else:
            messages.append({"role": "user", "content": user_prompt})

        response = client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )

        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        ).strip()

        return text if text else None

    except Exception as e:
        _log_llm_error(
            "Anthropic",
            _cfg(config, "ANTHROPIC_MODEL", "?"),
            e,
        )
        return None


def diagnose_gemini(config):

    api_key = _cfg(config, "GEMINI_API_KEY", "")

    if not api_key:
        return {
            "ok": False,
            "error": "No GEMINI_API_KEY found in .env"
        }

    try:
        import google.generativeai as genai
    except ImportError as e:
        return {
            "ok": False,
            "error": f"google-generativeai not installed: {e}"
        }

    try:
        genai.configure(api_key=api_key)

        configured_model = _cfg(
            config,
            "GEMINI_MODEL",
            "gemini-2.0-flash"
        )

        candidates = [configured_model]

        for model in _GEMINI_MODEL_FALLBACKS:
            if model not in candidates:
                candidates.append(model)

        last_error = None

        for model_name in candidates:

            try:
                print(f"[AI Assistant] Testing model: {model_name}")

                model = genai.GenerativeModel(model_name)

                response = model.generate_content(
                    "Reply only with OK",
                    generation_config={
                        "max_output_tokens": 10,
                        "temperature": 0
                    },
                )

                text = getattr(response, "text", "")

                if text and text.strip():
                    return {
                        "ok": True,
                        "model_used": model_name,
                        "sample_reply": text.strip()
                    }

            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"

        return {
            "ok": False,
            "error": last_error or "All Gemini models failed"
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }


def call_llm(system_prompt,
             user_prompt,
             config,
             max_tokens=700,
             temperature=0.4,
             history=None,
             images=None):

    result = call_gemini(
        system_prompt,
        user_prompt,
        config,
        max_tokens,
        temperature,
        history=history,
        images=images,
    )

    if result:
        return result

    return call_anthropic(
        system_prompt,
        user_prompt,
        config,
        max_tokens,
        history=history,
        images=images,
    )