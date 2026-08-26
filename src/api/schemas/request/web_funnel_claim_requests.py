"""Versioned request contracts for the paid web-funnel claim flow."""

from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from src.api.schemas.request.onboarding_requests import OnboardingCompleteRequest


class WebFunnelOnboardingSnapshot(OnboardingCompleteRequest):
    """Immutable mobile-compatible onboarding snapshot collected before checkout.

    Date of birth is the only age authority.  The endpoint derives age server-side
    when it needs a preview or writes a profile during authenticated completion.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_birth_date(self):
        try:
            birth_date = date(self.birth_year, self.birth_month, self.birth_day)
        except ValueError as exc:
            raise ValueError("Invalid birth date") from exc

        today = date.today()
        age = (
            today.year
            - birth_date.year
            - ((today.month, today.day) < (birth_date.month, birth_date.day))
        )
        if not 13 <= age <= 120:
            raise ValueError("Birth date must produce an age between 13 and 120")
        return self

    @property
    def birth_date(self) -> date:
        """Return the validated canonical date of birth."""
        return date(self.birth_year, self.birth_month, self.birth_day)


class WebFunnelLeadCreateRequest(BaseModel):
    """Possession-bound pre-checkout lead payload sent by the web BFF."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    payload: WebFunnelOnboardingSnapshot


class WebFunnelClaimExchangeRequest(BaseModel):
    """Opaque credentials supplied only by the magic-link mobile handoff."""

    model_config = ConfigDict(extra="forbid")

    magic_token: str = Field(min_length=32, max_length=512)
    client_retry_secret: str = Field(min_length=43, max_length=512)


class WebFunnelClaimCompleteRequest(BaseModel):
    """No identity or onboarding data is accepted after a claim exchange."""

    model_config = ConfigDict(extra="forbid")

    exchange_token: str = Field(min_length=32, max_length=512)


class WebFunnelRevenueCatCorrelationRequest(BaseModel):
    """Untrusted anonymous customer hint sent only by the web BFF."""

    model_config = ConfigDict(extra="forbid")

    app_user_id: str = Field(min_length=1, max_length=255)
    redemption_link_hash: str = Field(
        min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$"
    )


class WebFunnelRedemptionFinalizeRequest(BaseModel):
    """Explicit user consent before applying a verified web purchase."""

    model_config = ConfigDict(extra="forbid")

    confirm_apply_purchase: bool
    redemption_link_hash: str = Field(
        min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$"
    )


class WebFunnelRedemptionPreflightRequest(BaseModel):
    """Non-secret digest used to check account eligibility before redemption."""

    model_config = ConfigDict(extra="forbid")

    redemption_link_hash: str = Field(
        min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$"
    )
