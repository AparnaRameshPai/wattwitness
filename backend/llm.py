"""LLM client for Vultr Serverless Inference (OpenAI-compatible). The API key is used here and nowhere else.

Every call is measured (model, role, tokens, time, cost) and streamed to the website's Vultr sidebar via telemetry.
"""
import json
import re
import time
import uuid

from openai import AsyncOpenAI

from . import config, telemetry

_client = AsyncOpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY or "missing")


TRUNCATED_NOTE = "# [TRUNCATED: this reply hit the token limit; write a shorter, complete script]"


async def chat(system: str, user, model: str | None = None, temperature: float = 0.2, role: str = "agent") -> str:
    """`user` is a string, or a list of OpenAI content parts (text + images) for vision models.
    `role` says which agent is calling (planner, coder, verifier, ...), for the usage meter.
    Reasoning models spend tokens thinking before they answer, so the limit is generous (LLM_MAX_TOKENS).
    A reply cut off by the limit gets a marker line, so the fixer knows to write something shorter."""
    model = model or config.LLM_MODEL
    call_id = uuid.uuid4().hex[:8]
    await telemetry.emit({"type": "llm_start", "call_id": call_id, "role": role, "model": model})
    start = time.monotonic()
    try:
        resp = await _client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=config.LLM_MAX_TOKENS,
        )
    except Exception:
        await _report(call_id, role, model, start, None, ok=False)
        raise
    await _report(call_id, role, model, start, resp.usage, ok=True)
    choice = resp.choices[0]
    text = choice.message.content or ""
    if choice.finish_reason == "length" and text:
        text += "\n" + TRUNCATED_NOTE
    return text


async def _report(call_id: str, role: str, model: str, start: float, usage, ok: bool) -> None:
    seconds = round(time.monotonic() - start, 2)
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    usd = telemetry.record(model, role, prompt, completion, seconds, ok)
    await telemetry.emit({"type": "llm_call", "call_id": call_id, "role": role, "model": model, "ok": ok,
                          "prompt_tokens": prompt, "completion_tokens": completion, "seconds": seconds,
                          "cost_usd": usd})


def extract_code(text: str) -> str:
    """Take the first ```python block. If the closing fence is missing (reply cut off), take everything after the
    opening fence; with no fence at all, take the whole reply."""
    match = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    if match:
        return match.group(1).strip()
    opening = re.search(r"```(?:python|py)?\s*\n", text)
    return (text[opening.end():] if opening else text).strip()


def extract_json(text: str):
    """Parse the first JSON object or array in a reply (models often wrap JSON in prose or fences)."""
    fenced = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found in model reply")
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    return json.loads(text[start : end + 1])


async def chat_json(system: str, user, model: str | None = None, role: str = "agent"):
    """Ask for JSON; if the reply does not parse, ask once more with the parse error."""
    reply = await chat(system, user, model, role=role)
    try:
        return extract_json(reply)
    except (ValueError, json.JSONDecodeError) as exc:
        nudge = f"Your previous reply was not valid JSON ({exc}). Reply with valid JSON only."
        retry_user = f"{user}\n\n{nudge}" if isinstance(user, str) else [*user, {"type": "text", "text": nudge}]
        return extract_json(await chat(system, retry_user, model, role=role))


async def review_image(system: str, text: str, image_b64: str, model: str, role: str = "chart-critic"):
    """Send a PNG to a vision-capable model and get JSON back."""
    content = [{"type": "text", "text": text},
               {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}]
    return await chat_json(system, content, model, role=role)
