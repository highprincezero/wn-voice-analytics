from dataclasses import dataclass, field

from app.config import get_settings

JAILBREAK_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the system",
    "you are now",
    "reveal your prompt",
    "reveal the system prompt",
    "system prompt:",
    "jailbreak",
    "do anything now",
)

HARM_PATTERNS = {
    "Violence": ("build a bomb", "make a bomb"),
    "SelfHarm": ("kill yourself", "how to commit suicide"),
    "Hate": ("kill all",),
}


@dataclass
class SafetyResult:
    attack_detected: bool = False
    categories: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.attack_detected or bool(self.categories)

    @property
    def reason(self) -> str:
        parts: list[str] = []
        if self.attack_detected:
            parts.append("prompt injection detected")
        if self.categories:
            parts.append("content categories: " + ", ".join(self.categories))
        return "; ".join(parts)


class MockSafety:
    def shield_prompt(self, text: str) -> SafetyResult:
        lowered = text.lower()
        attack = any(pattern in lowered for pattern in JAILBREAK_PATTERNS)
        return SafetyResult(attack_detected=attack, categories=[])

    def analyze_content(self, text: str) -> SafetyResult:
        shield = self.shield_prompt(text)
        lowered = text.lower()
        categories = [
            name
            for name, patterns in HARM_PATTERNS.items()
            if any(pattern in lowered for pattern in patterns)
        ]
        return SafetyResult(attack_detected=shield.attack_detected, categories=categories)


class AzureSafety:
    def _post(self, path: str, payload: dict) -> dict:
        import httpx

        settings = get_settings()
        if not settings.azure_content_safety_endpoint or not settings.azure_content_safety_key:
            raise RuntimeError("Azure AI Content Safety is not configured")
        url = f"{settings.azure_content_safety_endpoint.rstrip('/')}{path}"
        headers = {
            "Ocp-Apim-Subscription-Key": settings.azure_content_safety_key,
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()

    def shield_prompt(self, text: str) -> SafetyResult:
        data = self._post(
            "/contentsafety/text:shieldPrompt?api-version=2024-09-01",
            {"userPrompt": text[:10000], "documents": []},
        )
        attack = bool((data.get("userPromptAnalysis") or {}).get("attackDetected"))
        return SafetyResult(attack_detected=attack, categories=[])

    def analyze_content(self, text: str) -> SafetyResult:
        shield = self.shield_prompt(text)
        data = self._post(
            "/contentsafety/text:analyze?api-version=2024-09-01",
            {
                "text": text[:10000],
                "categories": ["Hate", "Sexual", "SelfHarm", "Violence"],
                "outputType": "FourSeverityLevels",
            },
        )
        threshold = get_settings().content_safety_block_severity
        categories: list[str] = []
        for item in data.get("categoriesAnalysis") or []:
            if int(item.get("severity") or 0) >= threshold:
                categories.append(str(item.get("category")))
        return SafetyResult(attack_detected=shield.attack_detected, categories=categories)


def get_safety() -> MockSafety | AzureSafety:
    if get_settings().safety_provider == "azure":
        return AzureSafety()
    if get_settings().safety_provider == "mock":
        return MockSafety()
    raise RuntimeError("SAFETY_PROVIDER must be mock or azure")
