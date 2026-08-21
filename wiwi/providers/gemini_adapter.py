"""Google Gemini adapter: generateContent REST + alt=sse streaming."""

from __future__ import annotations

import json
from typing import Any

from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.streaming import deltas as dl


class GeminiAdapter:
    provider_type = "gemini"

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        return {}  # key goes in querystring

    def build_url(self, base_url: str, model_id: str, stream: bool, kind: str) -> str:
        base = base_url.rstrip("/") or "https://generativelanguage.googleapis.com/v1beta"
        method = "streamGenerateContent?alt=sse&key=" if stream else "generateContent?key="
        return f"{base}/models/{model_id}:{method}"

    def encode_request(self, req: IRRequest, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        g = req.gen_params
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for m in req.messages:
            if m.role == "system":
                system_parts.extend(p.text for p in m.parts if isinstance(p, ir.TextPart))
                continue
            role = "model" if m.role == "assistant" else "user"
            parts: list[dict[str, Any]] = []
            for p in m.parts:
                if isinstance(p, ir.TextPart):
                    parts.append({"text": p.text})
                elif isinstance(p, ir.ImagePart):
                    if p.b64:
                        parts.append({"inline_data": {"mime_type": p.mime, "data": p.b64}})
                    elif p.url:
                        parts.append({"file_data": {"file_uri": p.url}})
                elif isinstance(p, ir.ToolUsePart):
                    parts.append({"functionCall": {"name": p.name, "args": p.args}})
                elif isinstance(p, ir.ToolResultPart):
                    try:
                        import json as _j
                        resp = _j.loads(p.content)
                    except Exception:
                        resp = {"result": p.content}
                    parts.append({"functionResponse": {"name": p.tool_use_id,
                                                       "response": resp}})
            if parts:
                if contents and contents[-1]["role"] == role:
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": role, "parts": parts})
        body: dict[str, Any] = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        gen: dict[str, Any] = {}
        if g.max_tokens:
            gen["maxOutputTokens"] = g.max_tokens
        if g.temperature is not None:
            gen["temperature"] = g.temperature
        if g.top_p is not None:
            gen["topP"] = g.top_p
        if g.stop:
            gen["stopSequences"] = g.stop
        if g.response_format and g.response_format.type == "json_object":
            gen["responseMimeType"] = "application/json"
        if gen:
            body["generationConfig"] = gen
        if req.tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description,
                 "parameters": t.parameters_json_schema}
                for t in req.tools
            ]}]
        return body

    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        data = json.loads(body)
        cand = (data.get("candidates") or [{}])[0]
        content = cand.get("content") or {}
        turn = ir.AssistantTurn(raw=data)
        for part in content.get("parts") or []:
            if "text" in part:
                turn.text += part["text"]
            elif "functionCall" in part:
                fc = part["functionCall"]
                turn.tool_calls.append(ir.ToolUsePart(
                    id=f"call_{fc.get('name', 'x')}", name=fc.get("name", ""),
                    args=fc.get("args") or {}))
        finish = cand.get("finishReason", "STOP")
        turn.stop_reason = {"STOP": "stop", "MAX_TOKENS": "length",
                            "SAFETY": "content_filter", "RECITATION": "content_filter",
                            }.get(finish, "stop")
        if turn.tool_calls:
            turn.stop_reason = "tool_call"
        u = data.get("usageMetadata") or {}
        turn.usage = ir.Usage(
            prompt_tokens=u.get("promptTokenCount", 0),
            completion_tokens=(u.get("candidatesTokenCount", 0)
                               + u.get("thoughtsTokenCount", 0)),
            cached_tokens=u.get("cachedContentTokenCount", 0),
            reasoning_tokens=u.get("thoughtsTokenCount", 0),
        )
        return turn

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return []
        out: list[dl.IRStreamDelta] = []
        if not getattr(self, "_started", False):
            out.append(dl.StreamStart(model=""))
            self._started = True
        cand = (payload.get("candidates") or [{}])[0]
        for part in (cand.get("content") or {}).get("parts") or []:
            if "text" in part:
                out.append(dl.TextDelta(part["text"]))
            elif "functionCall" in part:
                fc = part["functionCall"]
                out.append(dl.ToolCallOpen(index=0, id=f"call_{fc.get('name', 'x')}",
                                          name=fc.get("name", "")))
                out.append(dl.ToolCallArgsDelta(index=0,
                                                args_fragment=json.dumps(fc.get("args") or {})))
                out.append(dl.ToolCallClose(index=0))
        u = payload.get("usageMetadata")
        finish = cand.get("finishReason")
        if finish:
            if u:
                out.append(dl.UsageFinal(
                    prompt=u.get("promptTokenCount", 0),
                    cached=u.get("cachedContentTokenCount", 0),
                    reasoning=u.get("thoughtsTokenCount", 0),
                    output=(u.get("candidatesTokenCount", 0)
                            + u.get("thoughtsTokenCount", 0))))
            out.append(dl.Finish("tool_call" if "tool" in str(cand).lower() else
                                 {"STOP": "stop", "MAX_TOKENS": "length",
                                  "SAFETY": "content_filter"}.get(finish, "stop")))
            out.append(dl.StreamEnd())
        elif u:
            # keep latest usage for final frame
            self._last_usage = u
        return out

    def is_done(self, event: str, data: str) -> bool:
        return "finishReason" in data
