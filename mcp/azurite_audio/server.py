"""FastAPI app that exposes Azurite audio as MCP tools."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from contextlib import asynccontextmanager

from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".ogg", ".flac")
# Same ceiling as the API upload limit, so transcription can read the whole file.
MAX_INLINE_BYTES = 20 * 1024 * 1024

mcp = MCPServer(
    "azurite-audio",
    instructions=(
        "Read audio from the Azurite voice container. "
        "Call list_audio, then fetch_audio with one blob name."
    ),
    version="1",
)


def _container() -> str:
    return os.environ.get("AZURE_STORAGE_CONTAINER", "voice")


def _service() -> BlobServiceClient:
    connection = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    if not connection:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is required")
    return BlobServiceClient.from_connection_string(connection)


def _blob_name(value: str) -> str:
    key = (value or "").strip().lstrip("/")
    parts = key.split("/")
    if not key or any(part in {"", ".", ".."} for part in parts) or "\\" in key:
        raise ValueError("blob name is not allowed")
    return key


def _prefix(value: str) -> str:
    text = (value or "").strip().strip("/")
    if not text:
        return ""
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts) or "\\" in text:
        raise ValueError("prefix is not allowed")
    return text + "/"


def _is_audio(name: str) -> bool:
    return name.lower().endswith(AUDIO_SUFFIXES)


@mcp.tool()
def list_audio(prefix: str = "") -> str:
    """List audio blobs in the Azurite container.

    prefix narrows the listing, for example users/<id>/audio/.
    """
    start = _prefix(prefix)
    client = _service().get_container_client(_container())
    rows: list[dict[str, object]] = []
    for blob in client.list_blobs(name_starts_with=start or None):
        if not _is_audio(blob.name):
            continue
        content_type = None
        if blob.content_settings is not None:
            content_type = blob.content_settings.content_type
        rows.append(
            {
                "name": blob.name,
                "size": blob.size,
                "content_type": content_type,
            }
        )
    body = {"container": _container(), "count": len(rows), "blobs": rows}
    return json.dumps(body)


@mcp.tool()
def fetch_audio(blob_name: str) -> str:
    """Fetch one audio blob from Azurite by its blob name.

    Returns content type, size, sha256, and base64 audio.
    """
    key = _blob_name(blob_name)
    if not _is_audio(key):
        raise ValueError("blob is not an audio file")
    blob = _service().get_blob_client(_container(), key)
    props = blob.get_blob_properties()
    data = blob.download_blob().readall()
    if len(data) > MAX_INLINE_BYTES:
        raise ValueError("audio is larger than the MCP tool can return")
    content_type = None
    if props.content_settings is not None:
        content_type = props.content_settings.content_type
    payload = {
        "name": key,
        "container": _container(),
        "content_type": content_type,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "inlined": True,
        "audio_base64": base64.b64encode(data).decode("ascii"),
    }
    return json.dumps(payload)


_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1:*", "localhost:*", "azurite-mcp:*"],
)
_mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=_security,
)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Azurite audio MCP", lifespan=_lifespan)


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>azurite-audio</title>
<style>
  body { font: 16px/1.45 system-ui, sans-serif; margin: 2rem auto;
    max-width: 40rem; color: #1a1a1a; }
  code { font-family: ui-monospace, monospace; }
  dt { font-weight: 650; margin-top: 1rem; }
</style>
</head>
<body>
<h1>azurite-audio</h1>
<p>This server reads audio from the Azurite <code>voice</code> container.
The protocol endpoint is <code>POST /mcp</code>. A browser address bar cannot call it.</p>
<h2>Tools</h2>
<dl>
  <dt><code>list_audio</code></dt>
  <dd>Optional <code>prefix</code>, such as <code>users/&lt;id&gt;/audio/</code>.
  Lists each audio blob's name, size, and content type.</dd>
  <dt><code>fetch_audio</code></dt>
  <dd>Required <code>blob_name</code>.
  Returns the content type, size, sha256, and base64 audio.</dd>
</dl>
</body>
</html>
"""


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "server": "azurite-audio"}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return _PAGE


app.mount("/", _mcp_app)
