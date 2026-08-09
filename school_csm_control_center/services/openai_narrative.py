"""Optional OpenAI-assisted narrative generation.

The package is imported only when the operator chooses the OpenAI action. The
normal application and deterministic local generator therefore work without
the OpenAI SDK being installed. API keys are accepted in memory and are never
placed in prompts, return values, exception messages, or logs by this module.
"""

from __future__ import annotations

from copy import deepcopy
import importlib
import importlib.util
import json
from typing import Any, Callable, Mapping

from school_csm_control_center.services.narrative_report import (
    NARRATIVE_AI_OUTPUT_SCHEMA,
    SECTION_ORDER,
    build_narrative_source,
    compose_narrative_document,
)


class OpenAINarrativeError(RuntimeError):
    """Raised when optional assisted generation does not yield a safe report."""


class OpenAINarrativeUnavailableError(OpenAINarrativeError):
    """Raised when the optional SDK is not installed or cannot be initialized."""


class OpenAINarrativeClient:
    """Generate schema-constrained prose, then validate it against the snapshot."""

    DEFAULT_MODEL = "gpt-5.6-luna"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 60.0,
        client_factory: Callable[[str, float], Any] | None = None,
    ) -> None:
        self.model = " ".join(str(model or "").split())
        if not self.model:
            raise ValueError("OpenAI model is required.")
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._client_factory = client_factory
        self.last_response_metadata: dict[str, str] = {}

    @staticmethod
    def sdk_available() -> bool:
        try:
            return importlib.util.find_spec("openai") is not None
        except (ImportError, AttributeError, ValueError):
            return False

    def generate(
        self,
        snapshot: Mapping[str, Any],
        *,
        api_key: str,
    ) -> dict[str, Any]:
        key = str(api_key or "").strip()
        if not key:
            raise ValueError("An OpenAI API key is required for assisted generation.")
        self.last_response_metadata = {}
        source = build_narrative_source(snapshot)
        client = self._make_client(key)
        prompt = _source_prompt(source)
        try:
            response = client.responses.create(
                model=self.model,
                store=False,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Draft a formal School Client Satisfaction Measurement "
                            "narrative using only the supplied aggregate evidence. "
                            "Do not infer or invent figures. Do not identify individual "
                            "respondents. Return only the requested structured sections."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "school_csm_narrative_sections",
                        "strict": True,
                        "schema": deepcopy(NARRATIVE_AI_OUTPUT_SCHEMA),
                    }
                },
            )
        except Exception:
            # Do not include the provider exception: SDK and proxy exceptions
            # can contain request headers or other sensitive configuration.
            raise OpenAINarrativeError(
                "OpenAI-assisted generation could not be completed. The local "
                "narrative generator remains available."
            ) from None

        output_text = str(getattr(response, "output_text", "") or "").strip()
        if not output_text:
            raise OpenAINarrativeError(
                "OpenAI-assisted generation returned no narrative content."
            )
        try:
            value = json.loads(output_text)
        except json.JSONDecodeError:
            raise OpenAINarrativeError(
                "OpenAI-assisted generation returned content outside the required schema."
            ) from None
        if not isinstance(value, Mapping) or set(value) != set(SECTION_ORDER):
            raise OpenAINarrativeError(
                "OpenAI-assisted generation returned incomplete narrative sections."
            )
        try:
            document = compose_narrative_document(snapshot, value)
        except (TypeError, ValueError):
            # Numerical and structural validation errors are intentionally
            # collapsed at this boundary so unsafe model prose is never shown
            # as if it were a valid draft.
            raise OpenAINarrativeError(
                "OpenAI-assisted generation included content that is not supported "
                "by the preserved Dashboard snapshot."
            ) from None

        self.last_response_metadata = {
            "model": self.model,
            "response_id": str(getattr(response, "id", "") or ""),
        }
        return document

    def _make_client(self, key: str) -> Any:
        if self._client_factory is not None:
            try:
                return self._client_factory(key, self.timeout_seconds)
            except Exception:
                raise OpenAINarrativeUnavailableError(
                    "The optional OpenAI client could not be initialized."
                ) from None
        try:
            module = importlib.import_module("openai")
            client_type = getattr(module, "OpenAI")
            return client_type(api_key=key, timeout=self.timeout_seconds)
        except (ImportError, AttributeError):
            raise OpenAINarrativeUnavailableError(
                "OpenAI-assisted generation is not installed. The local narrative "
                "generator remains available."
            ) from None
        except Exception:
            raise OpenAINarrativeUnavailableError(
                "The optional OpenAI client could not be initialized."
            ) from None


def _source_prompt(source: Mapping[str, Any]) -> str:
    source_json = json.dumps(
        source,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "Use only the following immutable aggregate Dashboard evidence. "
        "Every number in the response must appear in this JSON.\n\n"
        f"{source_json}"
    )
