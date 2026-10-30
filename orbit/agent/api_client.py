"""Chat Completions calls that stay alive while a model is still working.

A non-streaming request sits silent until the whole answer is ready. Gateways
close that silence at about a minute, so a task died even though a tool had
just run. Streaming keeps the socket busy, and a dropped connection is tried
again instead of ending the task.
"""

import json
import socket
import time
import urllib.error
import urllib.request
from http.client import IncompleteRead

from orbit.agent.tools import api_error_text


class ApiError(Exception):
    """The service rejected the request. Retrying the same body will not help."""


class RetryableApiError(ApiError):
    """The connection stalled or broke before a complete reply arrived."""


class CancelledApi(Exception):
    """The user ended the task while the request was still running."""


class StreamAssembler:
    """Fold OpenAI-style stream deltas into one assistant message."""

    def __init__(self):
        self.content = []
        self.reasoning = []
        self.calls = {}
        self.finish = None
        self.saw_done = False

    def feed(self, event):
        """Return the phase label for this event, or None when it has no reply text."""
        if not isinstance(event, dict):
            return None
        error = event.get("error")
        choices = event.get("choices") or []
        if error and not choices:
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise ApiError(message or "接口返回错误")
        if not choices:
            return None
        choice = choices[0] or {}
        if choice.get("finish_reason"):
            self.finish = choice["finish_reason"]
        delta = choice.get("delta")
        if delta is None and choice.get("message"):
            delta = choice["message"]
        delta = delta or {}
        label = None
        text = delta.get("content")
        if isinstance(text, str) and text:
            self.content.append(text)
            label = "正在接收模型回复"
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            self.reasoning.append(reasoning)
            label = label or "正在思考"
        for call in delta.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            index = call.get("index", 0)
            slot = self.calls.get(index)
            if slot is None:
                slot = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                self.calls[index] = slot
            if call.get("id"):
                slot["id"] = call["id"]
            function = call.get("function") or {}
            if function.get("name"):
                slot["function"]["name"] += function["name"]
            if function.get("arguments"):
                slot["function"]["arguments"] += function["arguments"]
            label = "正在准备下一步操作"
        return label

    def message(self):
        assistant = {"role": "assistant", "content": "".join(self.content) or None}
        reasoning = "".join(self.reasoning)
        if reasoning:
            assistant["reasoning_content"] = reasoning
        if self.calls:
            assistant["tool_calls"] = [self.calls[index] for index in sorted(self.calls)]
        return assistant

    @property
    def complete(self):
        return self.saw_done or self.finish is not None


def _event_from_line(line):
    text = line.strip()
    if not text or text.startswith(b":"):
        return None
    if text.startswith(b"data:"):
        text = text[5:].strip()
        if text == b"[DONE]":
            return "[DONE]"
    if not text or not text.startswith(b"{"):
        return None
    try:
        return json.loads(text.decode("utf-8"))
    except ValueError as error:
        raise RetryableApiError("模型响应不完整") from error


def consume_lines(lines, on_progress=None, cancelled=None):
    """Assemble a reply from response lines, reporting each phase as it arrives."""
    assembler = StreamAssembler()
    json_body = []
    json_mode = None
    for line in lines:
        if cancelled and cancelled():
            raise CancelledApi()
        if isinstance(line, str):
            line = line.encode("utf-8")
        stripped = line.strip()
        if json_mode is None:
            if not stripped:
                continue
            json_mode = stripped.startswith(b"{")
        if json_mode:
            json_body.append(line)
            continue
        if stripped.startswith(b":"):
            if on_progress:
                on_progress("正在等待模型继续")
            continue
        event = _event_from_line(line)
        if event is None:
            continue
        if event == "[DONE]":
            assembler.saw_done = True
            break
        label = assembler.feed(event)
        if on_progress:
            on_progress(label or "正在等待模型继续")
    if json_mode:
        try:
            payload = json.loads(b"".join(json_body).decode("utf-8"))
        except ValueError as error:
            raise RetryableApiError("模型响应不完整") from error
        error = payload.get("error") if isinstance(payload, dict) else None
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if error and not choices:
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise ApiError(message or "接口返回错误")
        message = ((choices or [{}])[0] or {}).get("message")
        if not isinstance(message, dict):
            raise RetryableApiError("模型响应不完整")
        return message
    if not assembler.complete:
        raise RetryableApiError("模型响应中断")
    return assembler.message()


def _read_response(response, on_progress, cancelled):
    def lines():
        while True:
            if cancelled and cancelled():
                raise CancelledApi()
            line = response.readline()
            if not line:
                break
            yield line
            if line.strip().startswith(b"{"):
                rest = response.read()
                if rest:
                    yield rest
                break
    return consume_lines(lines(), on_progress, cancelled)


def retryable(error):
    if type(error) is RetryableApiError:
        return True
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (408, 409, 429, 500, 502, 503, 504)
    if isinstance(error, (TimeoutError, socket.timeout, ConnectionError, IncompleteRead)):
        return True
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, (TimeoutError, socket.timeout, ConnectionError)):
            return True
        text = str(reason).lower()
        return any(word in text for word in ("timed out", "timeout", "reset", "eof", "closed", "aborted"))
    return False


def request_completion(url, token, body, timeout, on_progress=None, cancelled=None):
    """Send one streaming completion. Raise RetryableApiError when it should be tried again."""
    payload = dict(body)
    payload["stream"] = True
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    last_notice = 0.0
    current_label = ""

    def report(label):
        nonlocal last_notice, current_label
        if not on_progress:
            return
        now = time.monotonic()
        if label != current_label or now - last_notice >= 1:
            current_label = label
            last_notice = now
            on_progress(label)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _read_response(response, report, cancelled)
    except CancelledApi:
        raise
    except RetryableApiError:
        raise
    except ApiError:
        raise
    except Exception as error:
        if retryable(error):
            raise RetryableApiError(api_error_text(error)) from error
        raise ApiError(api_error_text(error)) from error
