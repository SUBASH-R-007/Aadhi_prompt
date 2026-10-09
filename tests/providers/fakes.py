"""In-memory stand-ins for the google-genai and openai SDK clients."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import httpx2  # the HTTP client used by openai 3.x
from google.genai import errors as genai_errors
from google.genai import types as gtypes


def gemini_text_response(text: str, *, finish: str = "STOP", prompt_tokens: int = 100, out_tokens: int = 40,
                         thoughts: int = 10) -> gtypes.GenerateContentResponse:
    return gtypes.GenerateContentResponse(
        candidates=[
            gtypes.Candidate(
                content=gtypes.Content(role="model", parts=[gtypes.Part.from_text(text=text)]),
                finish_reason=getattr(gtypes.FinishReason, finish),
            )
        ],
        usage_metadata=gtypes.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens, candidates_token_count=out_tokens, thoughts_token_count=thoughts
        ),
    )


def gemini_blob_response(data: bytes, mime: str) -> gtypes.GenerateContentResponse:
    return gtypes.GenerateContentResponse(
        candidates=[
            gtypes.Candidate(
                content=gtypes.Content(role="model", parts=[gtypes.Part(inline_data=gtypes.Blob(data=data, mime_type=mime))]),
                finish_reason=gtypes.FinishReason.STOP,
            )
        ],
        usage_metadata=gtypes.GenerateContentResponseUsageMetadata(prompt_token_count=5, candidates_token_count=7),
    )


def gemini_prompt_blocked() -> gtypes.GenerateContentResponse:
    return gtypes.GenerateContentResponse(
        prompt_feedback=gtypes.GenerateContentResponsePromptFeedback(block_reason=gtypes.BlockedReason.SAFETY)
    )


def gemini_error(code: int, message: str, status: str = "", retry_delay: str | None = None) -> genai_errors.APIError:
    details: list[dict[str, Any]] = []
    if retry_delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    payload = {"error": {"code": code, "message": message, "status": status, "details": details}}
    cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    return cls(code, payload)


class FakeGeminiModels:
    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    def _next(self) -> Any:
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item()
        return item

    async def generate_content(self, *, model: str, contents: Any, config: Any = None) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._next()

    async def generate_images(self, *, model: str, prompt: str, config: Any = None) -> Any:
        self.calls.append({"model": model, "prompt": prompt, "config": config})
        return self._next()

    async def generate_videos(self, *, model: str, source: Any = None, config: Any = None, **kw: Any) -> Any:
        self.calls.append({"model": model, "source": source, "config": config, **kw})
        return self._next()


class FakeGeminiFiles:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.gets = 0
        self.downloads: list[Any] = []
        self.download_bytes = b""

    async def upload(self, *, file: Any, config: Any = None) -> Any:
        data = file.read()
        self.uploads.append({"size": len(data), "mime": config.mime_type, "name": config.display_name})
        return gtypes.File(name=f"files/f{len(self.uploads)}", uri=f"https://files.example/f{len(self.uploads)}",
                           mime_type=config.mime_type, state=gtypes.FileState.PROCESSING)

    async def get(self, *, name: str, config: Any = None) -> Any:
        self.gets += 1
        return gtypes.File(name=name, uri=f"https://files.example/{name}", mime_type="application/pdf",
                           state=gtypes.FileState.ACTIVE)

    async def download(self, *, file: Any, config: Any = None) -> bytes:
        self.downloads.append(file)
        return self.download_bytes


class FakeGeminiOperations:
    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls = 0

    async def get(self, operation: Any, *, config: Any = None) -> Any:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeGeminiClient:
    def __init__(self, key: str, script: list[Any] | None = None, ops: list[Any] | None = None) -> None:
        self.key = key
        self.aio = SimpleNamespace(
            models=FakeGeminiModels(script if script is not None else []),
            files=FakeGeminiFiles(),
            operations=FakeGeminiOperations(ops if ops is not None else []),
        )

    @property
    def models(self) -> FakeGeminiModels:
        return self.aio.models


def gemini_factory(scripts: dict[str, list[Any]], ops: dict[str, list[Any]] | None = None
                   ) -> tuple[Callable[[str], FakeGeminiClient], dict[str, FakeGeminiClient]]:
    """client_factory(key) returning one FakeGeminiClient per key (with its own script)."""
    clients: dict[str, FakeGeminiClient] = {}

    def factory(key: str) -> FakeGeminiClient:
        if key not in clients:
            clients[key] = FakeGeminiClient(key, scripts.setdefault(key, []), (ops or {}).setdefault(key, []))
        return clients[key]

    return factory, clients


# --- openai --------------------------------------------------------------------------------

_OPENAI_URL = "https://api.openai.com/v1/responses"


def openai_status_error(cls: type, status: int, message: str, *, code: str | None = None,
                        headers: dict[str, str] | None = None) -> Exception:
    response = httpx2.Response(status, request=httpx2.Request("POST", _OPENAI_URL), headers=headers or {})
    return cls(message, response=response, body={"code": code, "message": message} if code else None)


def openai_response(text: str, *, input_tokens: int = 50, output_tokens: int = 20, reasoning: int = 0,
                    incomplete: str | None = None, refusal: str | None = None) -> SimpleNamespace:
    content = [SimpleNamespace(type="output_text", text=text)]
    if refusal:
        content = [SimpleNamespace(type="refusal", refusal=refusal)]
    return SimpleNamespace(
        status="incomplete" if incomplete else "completed",
        incomplete_details=SimpleNamespace(reason=incomplete) if incomplete else None,
        output=[SimpleNamespace(type="message", content=content)],
        output_text="" if refusal else text,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
            input_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    )


#: In a fake stream's event list: the connection goes silent (until the caller gives up). As a
#: script item of ``FakeOpenAIResponses``: the request itself gets no answer (no headers).
STALL = object()


class FakeOpenAIStream:
    """``openai.AsyncStream`` stand-in: an async context manager iterating ``events``.

    A number in ``events`` is a pause (seconds), ``STALL`` a silence that never ends, an exception
    is raised at that point (e.g. a dropped connection).
    """

    def __init__(self, events: list[Any]) -> None:
        self.events = list(events)
        self.closed = False
        self.delivered = 0

    async def __aenter__(self) -> FakeOpenAIStream:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        self.closed = True
        return False

    async def close(self) -> None:
        self.closed = True

    async def _iterate(self) -> AsyncIterator[Any]:
        for item in self.events:
            if item is STALL:
                await asyncio.sleep(3600)
            elif isinstance(item, int | float):
                await asyncio.sleep(item)
            elif isinstance(item, BaseException):
                raise item
            else:
                self.delivered += 1
                yield item

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterate()


def openai_event(kind: str, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type=kind, **fields)


def openai_stream_events(response: Any, *, pause: float = 0.0) -> list[Any]:
    """The Responses API event sequence that ends with ``response`` (created, items, final event);
    ``pause`` seconds between events."""
    events: list[Any] = [openai_event("response.created", response=SimpleNamespace(status="in_progress", output=[]))]
    for i, item in enumerate(getattr(response, "output", None) or []):
        events += [openai_event("response.output_item.added", output_index=i, item=item),
                   openai_event("response.output_item.done", output_index=i, item=item)]
    final = "response.incomplete" if getattr(response, "status", "") == "incomplete" else "response.completed"
    events.append(openai_event(final, response=response))
    if pause:
        events = [x for ev in events for x in (pause, ev)]
    return events


def openai_chat_chunks(text: str, *, finish: str = "stop", refusal: str | None = None, prompt_tokens: int = 11,
                       completion_tokens: int = 7, pieces: int = 2) -> list[Any]:
    """Chat-completions stream chunks: the role, the content in ``pieces``, the finish reason, usage."""
    def chunk(**delta: Any) -> SimpleNamespace:
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**delta), finish_reason=None)], usage=None)

    chunks = [chunk(role="assistant", content="")]
    step = max(1, -(-len(text) // max(1, pieces)))
    chunks += [chunk(content=text[i:i + step]) for i in range(0, len(text), step)]
    if refusal:
        chunks.append(chunk(refusal=refusal))
    chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(), finish_reason=finish)], usage=None))
    chunks.append(SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=prompt_tokens,
                                                                    completion_tokens=completion_tokens)))
    return chunks


class FakeOpenAIResponses:
    """``client.responses``: each script item answers one request.

    A response object is streamed as its event sequence (``stream=True``, how the adapter calls it);
    a ``FakeOpenAIStream`` is returned as is; an exception is raised by the request; ``STALL`` never
    answers.
    """

    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if item is STALL:
            await asyncio.sleep(3600)
        if isinstance(item, BaseException):
            raise item
        if kwargs.get("stream") and not isinstance(item, FakeOpenAIStream):
            return FakeOpenAIStream(openai_stream_events(item))
        return item


class FakeOpenAISpeech:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.audio)


class FakeOpenAIFiles:
    def __init__(self) -> None:
        self.created: list[Any] = []

    async def create(self, *, file: Any, purpose: str) -> Any:
        self.created.append((file[0], len(file[1]), purpose))
        return SimpleNamespace(id=f"file-{len(self.created)}")


class FakeOpenAIClient:
    def __init__(self, script: list[Any] | None = None, audio: bytes = b"") -> None:
        self.responses = FakeOpenAIResponses(script or [])
        self.audio = SimpleNamespace(speech=FakeOpenAISpeech(audio))
        self.files = FakeOpenAIFiles()
