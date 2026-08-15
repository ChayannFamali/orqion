"""Mock OpenAI-совместимый сервер для нагрузочного тестирования (T-410 / BUG-007).

Слушает на localhost:8899, отвечает на:
  GET  /v1/models            — список моделей
  POST /v1/chat/completions  — обычный и потоковый режим

Использовался при диагностике BUG-007 (Windows). Не закоммичен ранее —
создан ad-hoc. Логируется каждый запрос для отладки.

Usage:
    python backend/scripts/mock_openai_server.py [--port 8899]
"""

from __future__ import annotations

import argparse
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

MOCK_MODEL_ID = "mock-model"
REQUEST_COUNTER = 0


def _make_chat_response(model: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "This is a mock response for load testing.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "total_tokens": 20,
        },
    }


def _make_stream_chunk(model: str, content: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content},
                "finish_reason": None,
            }
        ],
    }


def _make_stream_done(model: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }


app = FastAPI()


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": MOCK_MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "mock",
                }
            ],
        }
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: dict[str, Any]) -> Any:
    global REQUEST_COUNTER
    REQUEST_COUNTER += 1

    model = request.get("model", MOCK_MODEL_ID)
    stream = request.get("stream", False)

    if stream:

        async def _generate() -> AsyncIterator[str]:
            for token in ["This ", "is ", "a ", "mock ", "response ", "for ", "load ", "testing."]:
                import json

                chunk = _make_stream_chunk(model, token)
                yield f"data: {json.dumps(chunk)}\n\n"
            done = _make_stream_done(model)
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return JSONResponse(_make_chat_response(model))


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "requests": REQUEST_COUNTER})


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Mock OpenAI server for load testing")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8899, help="Bind port")
    args = parser.parse_args()

    print(f"Mock OpenAI server starting on {args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
