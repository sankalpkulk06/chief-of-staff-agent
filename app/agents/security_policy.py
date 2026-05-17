"""SecurityPolicy — declarative configuration for SecurityAgent."""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.config.settings import Settings

_DEFAULT_INJECTION_PATTERNS: List[str] = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now\b",
    r"forget\s+everything",
    r"\[SYSTEM\]",
    r"\boverride\s*:",
    r"act\s+as\s+an?\s+AI\s+with\s+no\b",
    r"###\s*New\s+[Ii]nstruction",
    (
        r"(pretend|roleplay|imagine)\s+(you\s+are|you're|to\s+be)\s+"
        r"(an?\s+)?(unrestricted|unfiltered|uncensored|evil|jailbreak)"
    ),
]

# Patterns compiled WITHOUT re.IGNORECASE (case matters for these specific names)
_DEFAULT_CASE_SENSITIVE_PATTERNS: List[str] = [
    r"\bDAN\b",  # specific jailbreak persona — "dan" in "Daniel" must not match
]


class SecurityPolicy(BaseModel):
    """All security configuration in one place.

    Pass an instance to SecurityAgent(policy=...) instead of individual kwargs.
    Use SecurityPolicy.from_settings(settings) to build from app config.
    """

    enabled: bool = True
    max_input_length: int = Field(default=2000, gt=0)
    max_output_length: int = Field(default=8000, gt=0)
    rate_limit_per_minute: int = Field(default=10, gt=0)
    rate_limit_enabled: bool = True
    html_sanitization_enabled: bool = True
    injection_patterns: List[str] = Field(
        default_factory=lambda: list(_DEFAULT_INJECTION_PATTERNS)
    )
    case_sensitive_injection_patterns: List[str] = Field(
        default_factory=lambda: list(_DEFAULT_CASE_SENSITIVE_PATTERNS)
    )
    pii_flag_types: List[str] = Field(
        default_factory=lambda: ["email", "phone", "ssn", "card"]
    )

    @classmethod
    def from_settings(cls, settings: "Settings") -> "SecurityPolicy":
        return cls(
            enabled=settings.security_enabled,
            max_input_length=settings.max_input_length,
            max_output_length=settings.max_output_length,
            rate_limit_per_minute=settings.rate_limit_per_minute,
            rate_limit_enabled=settings.rate_limit_enabled,
            html_sanitization_enabled=settings.html_sanitization_enabled,
        )
