from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    max_output_tokens: int = 4096
    temperature: float = 0.0

    @classmethod
    def from_env(cls, prefix: str = "OPENAI") -> ModelConfig:
        api_key = os.environ.get(f"{prefix}_API_KEY", "")
        base_url = os.environ.get(f"{prefix}_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get(f"{prefix}_MODEL", "gpt-4o")
        max_output_tokens = int(os.environ.get(f"{prefix}_MAX_OUTPUT_TOKENS", "4096"))
        temperature = float(os.environ.get(f"{prefix}_TEMPERATURE", "0.0"))
        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
