"""Gemini provider using the google-genai SDK."""

from __future__ import annotations

from google import genai
from google.genai import types

from draftforge.llm.base import LLMMessage, LLMProvider, TokenUsage


class GeminiProvider(LLMProvider):
    provider_name = "gemini"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = genai.Client(api_key=self.api_key)

    def _raw_complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> tuple[str, TokenUsage]:
        system_parts = [m.content for m in messages if m.role == "system"]
        system_instruction = "\n\n".join(system_parts) or None

        contents = [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part.from_text(text=m.content)],
            )
            for m in messages
            if m.role != "system"
        ]

        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system_instruction,
            response_mime_type="application/json" if json_mode else "text/plain",
        )

        resp = self._client.models.generate_content(
            model=model, contents=contents, config=config
        )

        text = resp.text or ""
        usage_meta = resp.usage_metadata
        prompt_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
        completion_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
        total_tokens = getattr(usage_meta, "total_token_count", None) or (
            prompt_tokens + completion_tokens
        )
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        return text, usage
