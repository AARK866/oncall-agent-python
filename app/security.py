import hashlib
import hmac

from fastapi import HTTPException, Request, status

from app.config import settings

API_KEY_HEADER = "x-api-key"
AUTHORIZATION_HEADER = "authorization"
WEBHOOK_SIGNATURE_HEADER = "x-oncall-signature"


async def require_api_token(request: Request) -> None:
    if not _api_auth_required():
        return

    expected_token = settings.api_token
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is enabled but API_TOKEN is not configured.",
        )

    provided_token = _extract_api_token(request)
    if not provided_token or not hmac.compare_digest(provided_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token.",
        )


async def require_webhook_auth(request: Request) -> None:
    if settings.webhook_secret:
        await verify_webhook_signature(request)
        return

    await require_api_token(request)


async def verify_webhook_signature(request: Request) -> None:
    secret = settings.webhook_secret
    if not secret:
        return

    provided_signature = request.headers.get(WEBHOOK_SIGNATURE_HEADER)
    if not provided_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing webhook signature.",
        )

    body = await request.body()
    expected_hex = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    expected_signature = f"sha256={expected_hex}"
    normalized_provided = provided_signature.strip()
    valid = hmac.compare_digest(normalized_provided, expected_signature) or hmac.compare_digest(
        normalized_provided,
        expected_hex,
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )


def validate_production_security() -> list[str]:
    missing: list[str] = []
    if settings.api_auth_enabled and not settings.api_token:
        missing.append("API_TOKEN")

    if not _is_production() or not settings.require_auth_in_production:
        return missing

    if not settings.api_token:
        missing.append("API_TOKEN")
    if not settings.webhook_secret:
        missing.append("WEBHOOK_SECRET")
    return sorted(set(missing))


def redact_text(text: str) -> str:
    redacted = text
    for secret in _configured_secrets():
        redacted = redacted.replace(secret, "***")
    return redacted


def _api_auth_required() -> bool:
    return settings.api_auth_enabled or (
        settings.require_auth_in_production and _is_production()
    )


def _is_production() -> bool:
    return settings.app_env.strip().lower() in {"prod", "production"}


def _extract_api_token(request: Request) -> str | None:
    header_token = request.headers.get(API_KEY_HEADER)
    if header_token:
        return header_token.strip()

    authorization = request.headers.get(AUTHORIZATION_HEADER, "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return None


def _configured_secrets() -> list[str]:
    return [
        secret
        for secret in [
            settings.api_token,
            settings.webhook_secret,
            settings.llm_api_key,
            settings.embedding_api_key,
            settings.github_token,
            settings.gitlab_token,
            settings.milvus_token,
        ]
        if secret
    ]
