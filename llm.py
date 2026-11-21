"""llm.py - stdlib-only OpenAI-compatible client for Luminaria.

Two modes:
  narrate()   - prose-only call (tools=[]), used on every normal turn.
  improvise() - bounded tool-calling sub-loop; engine stays authoritative.
"""

import json
import re
import urllib.request
import urllib.error

THINK_RE = re.compile(r"<think(?:ing)?[^>]*>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)


def strip_think(text):
    """Remove any chain-of-thought leakage the template didn't swallow."""
    if not text:
        return ""
    text = THINK_RE.sub("", text)
    # Handle unclosed  blocks too.
    text = re.sub(r"<\|?think(?:ing)?\|?>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


class LLMClient:
    def __init__(self, api_url, model, api_key="none", timeout=120,
                 temperature=0.8, max_tokens=300, disable_thinking=True):
        self.url = api_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.disable_thinking = disable_thinking

    def chat(self, messages, tools=None, tool_choice=None, max_tokens=None):
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": False,
        }
        if self.disable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice or "auto"
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            return {"error": f"LLM unreachable: {exc}"}
        except (json.JSONDecodeError, OSError) as exc:
            return {"error": f"LLM bad response: {exc}"}
        try:
            choice = payload["choices"][0]["message"]
        except (KeyError, IndexError):
            return {"error": "LLM malformed payload"}
        return {
            "content": choice.get("content") or "",
            "tool_calls": choice.get("tool_calls") or [],
        }

    # ---- mode A: narration only -------------------------------------------
    def narrate(self, system_prompt, user_prompt, max_tokens=None):
        res = self.chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            tools=None, max_tokens=max_tokens,
        )
        if "error" in res:
            return None, res["error"]
        return strip_think(res["content"]), None

    # ---- mode B: bounded improvise sub-loop --------------------------------
    def improvise(self, messages, tools, dispatch, max_steps=5):
        """Run the tool loop. `dispatch(name, args)` must return a string result.

        Returns (final_text, log_lines). Engine is authoritative: the LLM may
        only call tools that already exist on GameEngine.
        """
        log = []
        msgs = list(messages)
        for step in range(max_steps):
            res = self.chat(msgs, tools=tools, tool_choice="auto")
            if "error" in res:
                return None, log + [f"[llm error] {res['error']}"]
            if res["tool_calls"]:
                # Append assistant turn with its tool calls, then run each one.
                msgs.append({"role": "assistant",
                             "content": res["content"] or "",
                             "tool_calls": res["tool_calls"]})
                for tc in res["tool_calls"]:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = dispatch(name, args)
                    log.append(f"{name}({', '.join(f'{k}={v}' for k, v in args.items())}) -> {str(result)[:120]}")
                    msgs.append({"role": "tool",
                                 "tool_call_id": tc.get("id", f"call_{step}"),
                                 "name": name,
                                 "content": str(result)})
            else:
                return strip_think(res["content"]), log
        return "(improvise budget exhausted - the moment passes.)", log
