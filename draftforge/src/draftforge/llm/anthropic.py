"""Anthropic provider (Messages API)."""

from __future__ import annotations

import anthropic

from draftforge.llm.base import LLMMessage, LLMProvider, TokenUsage


class AnthropicProvider(LLMProvider):
    provider_name = "anthropic"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)

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
        system = "\n\n".join(system_parts) or None
        chat_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]

        # Anthropic has no dedicated JSON mode: instruct via system prompt and
        # prefill the assistant turn with "{" to bias the model toward valid JSON.
        prefill = None
        if json_mode:
            json_instruction = (
                "You must respond with valid JSON only, no prose, no markdown fences."
            )
            system = f"{system}\n\n{json_instruction}" if system else json_instruction
            prefill = "{"

        if prefill is not None:
            chat_messages = [*chat_messages, {"role": "assistant", "content": prefill}]

        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=chat_messages,
        )
        if system:
            kwargs["system"] = system

        resp = self._client.messages.create(**kwargs)

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        if prefill is not None and not text.startswith(prefill):
            text = prefill + text

        usage = TokenUsage(
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            total_tokens=resp.usage.input_tokens + resp.usage.output_tokens,
        )
        return text, usage
