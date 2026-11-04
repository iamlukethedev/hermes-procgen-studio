#!/usr/bin/env python3
"""A tiny OpenAI-compatible endpoint that replays scripted model replies.

Used to exercise the full Hermes -> image_generate -> Procgen Studio -> ctx.llm path on a
machine without model credentials (CI, a fresh VM). It is NOT a model:

* agent turns that offer the ``image_generate`` tool get a tool call carrying the user's
  message as the prompt, then a one-line final answer quoting the returned image path;
* Procgen Studio drawing requests get the matching technique program from
  ``procgen_studio/techniques`` (chosen by the same keyword picker the plugin uses);
* critic requests get a fixed passing verdict (the replay cannot look at images);
* anything else (titles, summaries) gets a short plain reply.

Run:  python3 scripts/replay_llm.py --port 8765
Then: model.provider=custom, model.base_url=http://127.0.0.1:8765/v1, model.default=replay
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from procgen_studio.prompts import TECHNIQUES_DIR, pick_techniques  # noqa: E402

LOG: list = []


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _aspect(prompt: str) -> str:
    p = prompt.lower()
    if re.search(r"\b(scene|landscape|wide|banner|hero image)\b", p):
        return "landscape"
    if re.search(r"\b(portrait|poster|tall)\b", p):
        return "portrait"
    return "square"


def _drawing(messages) -> str:
    user = _text(messages[-1].get("content"))
    m = re.search(r"(?:REQUEST|ORIGINAL REQUEST|USER REQUEST):\s*(.+)", user)
    request = m.group(1) if m else user
    animated = "This is an ANIMATION" in user
    name = pick_techniques(request, animated=animated, limit=1)[0]
    if animated:
        name = "logo_motion.py"
    code = (TECHNIQUES_DIR / name).read_text(encoding="utf-8")
    fence = "pixel" if name.endswith(".json") else "python"
    LOG.append({"kind": "drawing", "technique": name, "request": request[:120]})
    return f"```{fence}\n{code}\n```"


def _critique() -> str:
    LOG.append({"kind": "critic"})
    return json.dumps({"score": 8, "matches_prompt": True, "follows_style": True,
                       "issues": [], "fixes": []})


def respond(body: dict) -> dict:
    messages = body.get("messages") or []
    tools = body.get("tools") or []
    system = "\n".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
    all_text = "\n".join(_text(m.get("content")) for m in messages)
    tool_names = {t.get("function", {}).get("name") for t in tools if isinstance(t, dict)}
    if "You are Procgen Studio" in system:
        return {"content": _drawing(messages)}
    if "art director" in all_text:
        return {"content": _critique()}
    if tools and ("image_generate" in tool_names or "video_generate" in tool_names):
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            try:
                payload = json.loads(_text(last.get("content")))
            except ValueError:
                payload = {}
            out = payload.get("image") or payload.get("video") or payload.get("error")
            LOG.append({"kind": "final", "result": str(out)[:200]})
            return {"content": f"Done: {out}"}
        user = next((_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
        wants_video = "video_generate" in tool_names and re.search(r"\b(animate|animated|video|mp4|motion)\b",
                                                                   user, re.I)
        name = "video_generate" if wants_video else "image_generate"
        args = {"prompt": user.strip()}
        if name == "image_generate":
            args["aspect_ratio"] = _aspect(user)
        else:
            args.update(aspect_ratio="1:1", duration=3, resolution="720p")
        LOG.append({"kind": "tool_call", "tool": name, "args": args})
        return {"tool_calls": [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)}}]}
    return {"content": "Procgen Studio demo"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, payload) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": "replay", "object": "model", "owned_by": "local",
                                                         "context_length": 200000}]})
        elif self.path.rstrip("/").endswith("/log"):
            self._send(200, LOG)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": f"unsupported path {self.path}"}})
            return
        msg = respond(body)
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        model = body.get("model") or "replay"
        finish = "tool_calls" if msg.get("tool_calls") else "stop"
        usage = {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def chunk(delta, finish_reason=None, with_usage=False):
                c = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
                if with_usage:
                    c["usage"] = usage
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())

            chunk({"role": "assistant", "content": ""})
            if msg.get("tool_calls"):
                tc = msg["tool_calls"][0]
                chunk({"tool_calls": [{"index": 0, "id": tc["id"], "type": "function",
                                       "function": {"name": tc["function"]["name"],
                                                    "arguments": tc["function"]["arguments"]}}]})
            else:
                text = msg["content"]
                for i in range(0, len(text), 2000):
                    chunk({"content": text[i:i + 2000]})
            chunk({}, finish, with_usage=True)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        message = {"role": "assistant", "content": msg.get("content")}
        if msg.get("tool_calls"):
            message["tool_calls"] = msg["tool_calls"]
        self._send(200, {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
                         "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ns = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", ns.port), Handler)
    print(f"replay endpoint on http://127.0.0.1:{ns.port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
