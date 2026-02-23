#!/usr/bin/env python3
"""Mock LLM upstream server for OnGarde demo.

Runs on port 4243 and responds like OpenAI/Anthropic — no real API key needed.
OnGarde proxies to this server, scans requests, and blocks/allows them.

Usage:
    python3 demo/mock_upstream.py
"""

import json
import time
import random
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

app = FastAPI(title="Mock LLM Upstream (OnGarde Demo)")

# Canned LLM responses
RESPONSES = [
    "I'm your AI assistant! How can I help you today?",
    "That's a great question. I'm happy to assist with that.",
    "Sure! Here's what I know about that topic...",
    "Let me think about that for a moment. The answer is straightforward.",
    "Absolutely! I can help you with that request.",
    "Interesting question! Here's my perspective on that.",
    "I understand what you're looking for. Let me provide a helpful response.",
    "Of course! That's something I can definitely assist with.",
]


def make_chat_response(content: str) -> dict:
    """Build a minimal OpenAI-compatible chat completion response."""
    return {
        "id": f"chatcmpl-demo-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-4o-mock",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 42,
            "completion_tokens": len(content.split()),
            "total_tokens": 42 + len(content.split()),
        },
    }


async def stream_response(content: str):
    """Stream a response token by token (SSE format)."""
    words = content.split()
    for i, word in enumerate(words):
        chunk = {
            "id": f"chatcmpl-demo-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "gpt-4o-mock",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": word + (" " if i < len(words) - 1 else "")},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        await asyncio.sleep(0.05)  # Simulate realistic token speed

    # Final chunk
    done_chunk = {
        "id": f"chatcmpl-demo-{int(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "gpt-4o-mock",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done_chunk)}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    stream = body.get("stream", False)
    content = random.choice(RESPONSES)

    if stream:
        return StreamingResponse(
            stream_response(content),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        return JSONResponse(make_chat_response(content))


@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    """Anthropic Messages API compatibility."""
    content = random.choice(RESPONSES)
    return JSONResponse({
        "id": f"msg_demo_{int(time.time())}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": content}],
        "model": "claude-3-5-sonnet-mock",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 42, "output_tokens": len(content.split())},
    })


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-llm-upstream"}


if __name__ == "__main__":
    print("🤖 Mock LLM upstream starting on http://127.0.0.1:4243")
    print("   (This is the fake LLM that OnGarde proxies to)")
    uvicorn.run(app, host="127.0.0.1", port=4243, log_level="warning")
