import httpx


# Raised for any non-2xx response so the UI can show the server's error message.
class ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# Thin wrapper over the backend REST API (one method per endpoint).
class ApiClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self) -> dict[str, str]:
        # Sends the login token as 'Authorization: Bearer <token>' once logged in.
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    # Single place every call goes through: builds the URL, adds auth, handles errors.
    def _request(self, method: str, path: str, timeout: float = 60, **kwargs) -> httpx.Response:
        # httpx.request(method, url, ...): one-off synchronous HTTP call (no client reuse).
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                timeout=timeout,
                # **kwargs passes json=/params=/files= through to httpx.
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ApiError(
                504, "That is taking longer than expected. Try again in a moment."
            ) from exc
        except httpx.TransportError as exc:
            raise ApiError(503, "The server is not reachable right now.") from exc
        # is_success = any 2xx status.
        if response.is_success:
            return response
        detail = response.text
        try:
            # FastAPI errors look like {"detail": ...}; pull that out for a readable message.
            body = response.json()
            parsed = body.get("detail", detail)
            # 422 validation errors come back as a list of {msg, loc, ...} items.
            if isinstance(parsed, list):
                detail = "; ".join(
                    item.get("msg", str(item)) if isinstance(item, dict) else str(item)
                    for item in parsed
                )
            else:
                detail = str(parsed)
        except Exception:
            detail = response.text
        raise ApiError(response.status_code, detail)

    def meta(self) -> dict:
        return self._request("GET", "/api/v1/meta").json()

    def signup(self, email: str, password: str) -> dict:
        return self._request(
            "POST", "/api/v1/auth/signup", json={"email": email, "password": password}
        ).json()

    def login(self, email: str, password: str) -> dict:
        return self._request(
            "POST", "/api/v1/auth/login", json={"email": email, "password": password}
        ).json()

    def upload(self, files: list[tuple[str, bytes]]) -> dict:
        # Multipart upload: repeated 'files' fields, each a (filename, bytes, content-type) tuple.
        payload = [("files", (name, data, "audio/wav")) for name, data in files]
        return self._request("POST", "/api/v1/files", files=payload).json()

    def list_files(self, params: dict) -> dict:
        # Drop empty filters so they aren't sent as query params.
        cleaned = {key: value for key, value in params.items() if value not in (None, "")}
        return self._request("GET", "/api/v1/files", params=cleaned).json()

    def get_file(self, file_id: str) -> dict:
        return self._request("GET", f"/api/v1/files/{file_id}").json()

    def download_audio(self, file_id: str) -> tuple[bytes, str]:
        response = self._request("GET", f"/api/v1/files/{file_id}/audio")
        media = response.headers.get("content-type", "audio/wav").split(";")[0].strip()
        return response.content, media or "audio/wav"

    def list_events(self, limit: int = 300) -> dict:
        return self._request("GET", "/api/v1/events", params={"limit": limit}).json()

    def prompt_options(self) -> list[dict]:
        return self._request("GET", "/api/v1/prompts/options").json()["options"]

    def get_config(self) -> dict:
        return self._request("GET", "/api/v1/prompts/config").json()

    def save_config(self, selections: list[dict]) -> dict:
        response = self._request("PUT", "/api/v1/prompts/config", json={"selections": selections})
        return response.json()

    def run_summary(self, body: dict) -> dict:
        return self._request("POST", "/api/v1/summaries", json=body).json()

    def start_report(self, groupings: list[str] | None = None) -> dict:
        body = {} if groupings is None else {"groupings": groupings}
        return self._request("POST", "/api/v1/reports", json=body).json()

    def get_report(self, report_id: str) -> dict:
        return self._request("GET", f"/api/v1/reports/{report_id}").json()

    def list_reports(self) -> dict:
        return self._request("GET", "/api/v1/reports").json()

    def chat(self, message: str, history: list[dict], session_id: str | None = None) -> dict:
        payload: dict = {"message": message, "history": history}
        if session_id:
            payload["session_id"] = session_id
        # A turn can run a summary over many files, so it gets a longer wait.
        return self._request("POST", "/api/v1/chat", timeout=180, json=payload).json()

    def chat_session(self) -> dict:
        return self._request("GET", "/api/v1/chat/session").json()

    def open_chat_session(self) -> dict:
        return self._request("POST", "/api/v1/chat/session").json()
