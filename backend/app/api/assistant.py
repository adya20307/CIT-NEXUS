import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.booking.natural_language import detect_language
from app.auth import AuthenticatedPrincipal, require_roles
from app.services.campus_context import (
    build_campus_context,
    serialize_campus_context,
)
from app.services.gemini_service import (
    GeminiAuthenticationError,
    GeminiConfigurationError,
    GeminiModelError,
    GeminiQuotaError,
    GeminiRequestError,
    GeminiTimeoutError,
    GeminiUnavailableError,
    generate_assistant_reply,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/assistant", tags=["AI Campus Assistant"])


class AssistantHistoryMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    role: Literal["user", "model"]
    content: str = Field(min_length=1, max_length=2000)


class AssistantChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=2000)
    history: list[AssistantHistoryMessage] = Field(
        default_factory=list,
        max_length=8,
    )

    @model_validator(mode="after")
    def validate_conversation(self):
        if len(self.message) + sum(len(item.content) for item in self.history) > 8000:
            raise ValueError("Message and conversation history exceed 8,000 characters.")
        expected_role = "user"
        for item in self.history:
            if item.role != expected_role:
                raise ValueError(
                    "History must alternate user and model messages, starting with user."
                )
            expected_role = "model" if expected_role == "user" else "user"
        if self.history and self.history[-1].role != "model":
            raise ValueError("Conversation history must end with a model response.")
        return self


class AssistantAgentRequest(AssistantChatRequest):
    pass


class BookingConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=36, max_length=36)
    confirmed: bool


@router.post("/chat")
def chat_with_campus_assistant(
    request: AssistantChatRequest,
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    del principal
    language = detect_language(request.message)
    history = [
        item.model_dump()
        for item in request.history
    ]
    context = build_campus_context(request.message, history)
    try:
        gemini_response = generate_assistant_reply(
            request.message,
            history,
            serialize_campus_context(context),
            language,
        )
    except GeminiConfigurationError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "The AI Campus Assistant is not configured. Set GEMINI_API_KEY "
                "in the backend environment."
            ),
        ) from error
    except GeminiAuthenticationError as error:
        logger.warning("Gemini rejected configured credentials or API access.")
        raise HTTPException(
            status_code=502,
            detail=(
                "Gemini rejected the configured credentials or API access. "
                "Check the backend key and Gemini API permissions."
            ),
        ) from error
    except GeminiModelError as error:
        logger.warning("Configured Gemini model is unavailable.")
        raise HTTPException(
            status_code=502,
            detail=(
                "Gemini could not resolve the configured model for this API key. "
                "Verify GEMINI_MODEL and that the key has Gemini API access."
            ),
        ) from error
    except GeminiRequestError as error:
        logger.warning("Gemini rejected the assistant request as invalid.")
        raise HTTPException(
            status_code=502,
            detail="Gemini rejected the assistant request. Please try again.",
        ) from error
    except GeminiQuotaError as error:
        logger.warning("Gemini quota/rate limit reached.")
        raise HTTPException(
            status_code=429,
            detail="The AI assistant is temporarily rate limited. Please try again later.",
        ) from error
    except GeminiTimeoutError as error:
        logger.warning("Gemini request timed out.")
        raise HTTPException(
            status_code=504,
            detail="The AI assistant timed out. Please try again.",
        ) from error
    except GeminiUnavailableError as error:
        logger.warning("Gemini service is unavailable.")
        raise HTTPException(
            status_code=502,
            detail="The AI assistant could not get a response. Please try again.",
        ) from error

    return {
        "answer": gemini_response.answer,
        "language": language,
        "model": gemini_response.model,
        "sources": context["sources"],
        "read_only": True,
    }


@router.post("/agent")
def chat_with_booking_agent(
    request: AssistantAgentRequest,
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    from app.services.gemini_agent_service import generate_booking_agent_reply

    language = detect_language(request.message)
    history = [item.model_dump() for item in request.history]
    context = build_campus_context(request.message, history)
    try:
        response = generate_booking_agent_reply(
            request.message,
            history,
            serialize_campus_context(context),
            language,
            principal,
        )
    except GeminiConfigurationError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "The AI Campus Assistant is not configured. Set GEMINI_API_KEY "
                "in the backend environment."
            ),
        ) from error
    except GeminiAuthenticationError as error:
        logger.warning("Gemini rejected configured credentials or API access.")
        raise HTTPException(
            status_code=502,
            detail="Gemini rejected the configured credentials or API access.",
        ) from error
    except GeminiModelError as error:
        logger.warning("Configured Gemini model is unavailable.")
        raise HTTPException(
            status_code=502,
            detail="Gemini could not resolve the configured model.",
        ) from error
    except GeminiRequestError as error:
        logger.warning("Gemini rejected the assistant request as invalid.")
        raise HTTPException(
            status_code=502,
            detail="Gemini rejected the assistant request. Please try again.",
        ) from error
    except GeminiQuotaError as error:
        logger.warning("Gemini quota/rate limit reached.")
        raise HTTPException(
            status_code=429,
            detail="The AI assistant is temporarily rate limited. Please try again later.",
        ) from error
    except GeminiTimeoutError as error:
        logger.warning("Gemini request timed out.")
        raise HTTPException(
            status_code=504,
            detail="The AI assistant timed out. Please try again.",
        ) from error
    except GeminiUnavailableError as error:
        logger.warning("Gemini service is unavailable.")
        raise HTTPException(
            status_code=502,
            detail="The AI assistant could not get a response. Please try again.",
        ) from error

    return {
        **response,
        "language": language,
        "sources": context["sources"],
        "read_only": False,
    }


@router.post("/confirm")
def confirm_booking_action(
    request: BookingConfirmationRequest,
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    from app.services.booking_agent_service import confirm_booking_proposal

    result = confirm_booking_proposal(
        request.proposal_id,
        request.confirmed,
        principal,
    )
    return result


@router.get("/proposals")
def get_booking_action_proposals(
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    from app.services.booking_agent_service import list_pending_booking_proposals

    return {
        "proposals": list_pending_booking_proposals(principal),
    }
