import httpx


class ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class ApiClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = httpx.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(),
            timeout=60,
            **kwargs,
        )
        if response.is_success:
            return response
        detail = response.text
        try:
            body = response.json()
            parsed = body.get("detail", detail)
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

    def me(self) -> dict:
        return self._request("GET", "/api/v1/auth/me").json()

    def upload(self, files: list[tuple[str, bytes]]) -> dict:
        payload = [("files", (name, data, "audio/wav")) for name, data in files]
        return self._request("POST", "/api/v1/files", files=payload).json()

    def list_files(self, params: dict) -> dict:
        cleaned = {key: value for key, value in params.items() if value not in (None, "")}
        return self._request("GET", "/api/v1/files", params=cleaned).json()

    def get_file(self, file_id: str) -> dict:
        return self._request("GET", f"/api/v1/files/{file_id}").json()

    def delete_file(self, file_id: str) -> None:
        self._request("DELETE", f"/api/v1/files/{file_id}")

    def prompt_options(self) -> list[dict]:
        return self._request("GET", "/api/v1/prompts/options").json()["options"]

    def get_config(self) -> dict:
        return self._request("GET", "/api/v1/prompts/config").json()

    def save_config(self, selections: list[dict]) -> dict:
        response = self._request("PUT", "/api/v1/prompts/config", json={"selections": selections})
        return response.json()

    def run_summary(self, body: dict) -> dict:
        return self._request("POST", "/api/v1/summaries", json=body).json()

    def list_summaries(self) -> dict:
        return self._request("GET", "/api/v1/summaries").json()

    def chat(self, message: str, history: list[dict]) -> dict:
        return self._request(
            "POST",
            "/api/v1/chat",
            json={"message": message, "history": history},
        ).json()
