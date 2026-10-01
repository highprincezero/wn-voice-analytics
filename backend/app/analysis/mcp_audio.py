"""Call the Azurite MCP server's fetch_audio tool."""

from __future__ import annotations

import base64
import json

import httpx


def fetch_audio_via_mcp(url: str, blob_name: str) -> bytes:
    """Read one audio blob by calling the MCP tool, then return the raw bytes."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "fetch_audio", "arguments": {"blob_name": blob_name}},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with httpx.Client(timeout=120) as client:
        response = client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    body = response.json()
    result = body.get("result") or {}
    content = result.get("content") or []
    text = ""
    if content and isinstance(content[0], dict):
        text = str(content[0].get("text") or "")
    if result.get("isError"):
        raise RuntimeError(text or "MCP fetch_audio failed")
    data = json.loads(text)
    encoded = str(data.get("audio_base64") or "")
    if not data.get("inlined") or not encoded:
        raise RuntimeError("MCP fetch_audio did not return the audio bytes")
    return base64.b64decode(encoded)
