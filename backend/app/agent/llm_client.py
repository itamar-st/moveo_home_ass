"""OpenRouter LLM client wrapper with structured output, fallbacks, and retries."""

import logging
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
)
from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMUnavailableError(Exception):
    """Raised when the LLM cannot be reached or no API key is configured."""


class LLMResponse(BaseModel):
    """Normalized response returned by an LLM client."""

    content: str
    model: str
    usage: dict[str, Any] | None = None


class LLMClientProtocol(Protocol):
    """Interface satisfied by OpenRouterLLMClient and test FakeLLM implementations."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Produce one assistant completion."""
        ...


def _is_retryable_openai_error(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError),
    ):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


class OpenRouterLLMClient:
    """AsyncOpenAI client configured for OpenRouter with fallback models and retries."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._primary_model = self._settings.openrouter_model
        self._fallbacks = self._settings.fallback_models_list

        models: list[str] = []
        for m in [self._primary_model, *self._fallbacks]:
            if m and m not in models:
                models.append(m)
        # OpenRouter enforces at most 3 models in the fallback `models` array.
        self._models = models[:3]

        self._client: AsyncOpenAI | None = None
        if self._settings.openrouter_api_key:
            self._client = AsyncOpenAI(
                base_url=OPENROUTER_BASE_URL,
                api_key=self._settings.openrouter_api_key,
                timeout=45.0,
                default_headers={
                    "HTTP-Referer": self._settings.openrouter_app_url,
                    "X-Title": self._settings.openrouter_app_name,
                },
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception(_is_retryable_openai_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _create_completion(
        self,
        *,
        model: str,
        models_list: list[str],
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None,
        require_parameters: bool,
    ) -> Any:
        assert self._client is not None
        extra_body: dict[str, Any] = {}
        if len(models_list) > 1:
            extra_body["models"] = models_list
        if require_parameters:
            extra_body["provider"] = {"require_parameters": True}

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        if extra_body:
            kwargs["extra_body"] = extra_body

        return await self._client.chat.completions.create(**kwargs)

    def _resolve_models(self, model: str | None) -> tuple[str, list[str]]:
        primary = model or self._primary_model
        models: list[str] = []
        for m in [primary, *self._fallbacks]:
            if m and m not in models:
                models.append(m)
        return primary, models[:3]

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Call OpenRouter chat completions with json_schema -> json_object -> plain fallback."""
        if self._client is None:
            raise LLMUnavailableError("OPENROUTER_API_KEY is not configured.")

        primary_model, models_list = self._resolve_models(model)

        try:
            response = await self._create_completion(
                model=primary_model,
                models_list=models_list,
                messages=messages,
                response_format=response_format,
                require_parameters=response_format is not None,
            )
        except (BadRequestError, NotFoundError) as exc:
            if response_format and response_format.get("type") == "json_schema":
                logger.warning(
                    "OpenRouter rejected json_schema for %s (%s); falling back to json_object",
                    primary_model,
                    exc,
                )
                try:
                    response = await self._create_completion(
                        model=primary_model,
                        models_list=models_list,
                        messages=messages,
                        response_format={"type": "json_object"},
                        require_parameters=False,
                    )
                except (BadRequestError, NotFoundError) as obj_exc:
                    logger.warning(
                        "OpenRouter rejected json_object for %s (%s); falling back to plain JSON prompt",
                        primary_model,
                        obj_exc,
                    )
                    try:
                        response = await self._create_completion(
                            model=primary_model,
                            models_list=models_list,
                            messages=messages,
                            response_format=None,
                            require_parameters=False,
                        )
                    except Exception as plain_exc:
                        raise LLMUnavailableError(f"OpenRouter request failed: {plain_exc}") from plain_exc
                except Exception as fallback_exc:
                    raise LLMUnavailableError(f"OpenRouter request failed: {fallback_exc}") from fallback_exc
            elif response_format is not None:
                logger.warning(
                    "OpenRouter rejected response_format for %s (%s); retrying without response_format",
                    primary_model,
                    exc,
                )
                try:
                    response = await self._create_completion(
                        model=primary_model,
                        models_list=models_list,
                        messages=messages,
                        response_format=None,
                        require_parameters=False,
                    )
                except Exception as plain_exc:
                    raise LLMUnavailableError(f"OpenRouter request failed: {plain_exc}") from plain_exc
            else:
                raise LLMUnavailableError(f"OpenRouter bad request: {exc}") from exc
        except Exception as exc:
            raise LLMUnavailableError(f"OpenRouter request failed: {exc}") from exc

        choices = getattr(response, "choices", None) or []
        if not choices or not getattr(choices[0], "message", None):
            raise LLMUnavailableError("OpenRouter returned empty choices.")

        content = choices[0].message.content or ""
        actual_model = getattr(response, "model", None) or primary_model
        usage_obj = getattr(response, "usage", None)
        usage_dict = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else None

        logger.info("llm_completion model=%s chars=%d", actual_model, len(content))
        return LLMResponse(content=content, model=actual_model, usage=usage_dict)
