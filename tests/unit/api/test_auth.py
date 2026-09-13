"""
Unit tests for authentication dependencies.

Tests cover Firebase token verification, user ID extraction,
email extraction, and optional authentication.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from firebase_admin import auth as firebase_auth

from src.api.dependencies.auth import (
    get_current_user_email,
    get_current_user_id,
    optional_authentication,
    resolve_current_user_id,
    verify_firebase_token,
)


def _auth_request() -> SimpleNamespace:
    request = SimpleNamespace()
    request.state = SimpleNamespace()
    return request


class TestVerifyFirebaseToken:
    """Tests for verify_firebase_token dependency."""

    @pytest.mark.asyncio
    async def test_verify_valid_token_success(self):
        """Test successful verification of valid Firebase token."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "valid_firebase_token"

        expected_decoded_token = {
            "uid": "test_firebase_uid_123",
            "email": "test@example.com",
            "email_verified": True,
        }

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.return_value = expected_decoded_token

            # Act
            mock_request = Mock()
            mock_request.state = Mock()
            result = await verify_firebase_token(mock_request, mock_credentials)

            # Assert
            assert result == expected_decoded_token
            assert result["uid"] == "test_firebase_uid_123"
            assert result["email"] == "test@example.com"
            mock_verify.assert_called_once_with("valid_firebase_token")

    @pytest.mark.asyncio
    async def test_verify_token_expired_error(self):
        """Test handling of expired Firebase token."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "expired_token"

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.side_effect = firebase_auth.ExpiredIdTokenError(
                "Token expired", cause=Exception("Expired")
            )

            # Act & Assert
            mock_request = Mock()
            mock_request.state = Mock()
            with pytest.raises(HTTPException) as exc_info:
                await verify_firebase_token(mock_request, mock_credentials)

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "expired" in exc_info.value.detail.lower()
            assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}

    @pytest.mark.asyncio
    async def test_verify_token_revoked_error(self):
        """Test handling of revoked Firebase token."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "revoked_token"

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.side_effect = firebase_auth.RevokedIdTokenError("Token revoked")

            # Act & Assert
            mock_request = Mock()
            mock_request.state = Mock()
            with pytest.raises(HTTPException) as exc_info:
                await verify_firebase_token(mock_request, mock_credentials)

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "revoked" in exc_info.value.detail.lower()
            assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}

    @pytest.mark.asyncio
    async def test_verify_token_invalid_error(self):
        """Test handling of invalid Firebase token."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "invalid_token"

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.side_effect = firebase_auth.InvalidIdTokenError("Invalid token")

            # Act & Assert
            mock_request = Mock()
            mock_request.state = Mock()
            with pytest.raises(HTTPException) as exc_info:
                await verify_firebase_token(mock_request, mock_credentials)

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "invalid" in exc_info.value.detail.lower()
            assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}

    @pytest.mark.asyncio
    async def test_verify_token_certificate_fetch_error(self):
        """Test handling of Firebase certificate fetch error."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "some_token"

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.side_effect = firebase_auth.CertificateFetchError(
                "Cannot fetch certificates", cause=Exception("Network error")
            )

            # Act & Assert
            mock_request = Mock()
            mock_request.state = Mock()
            with pytest.raises(HTTPException) as exc_info:
                await verify_firebase_token(mock_request, mock_credentials)

            assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            assert "unavailable" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_verify_token_unexpected_error(self):
        """Test handling of unexpected error during token verification."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "some_token"

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.side_effect = Exception("Unexpected error")

            # Act & Assert
            mock_request = Mock()
            mock_request.state = Mock()
            with pytest.raises(HTTPException) as exc_info:
                await verify_firebase_token(mock_request, mock_credentials)

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "failed to verify" in exc_info.value.detail.lower()
            assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}

    @pytest.mark.asyncio
    async def test_verify_token_with_custom_claims(self):
        """Test verification of token with custom claims."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "token_with_claims"

        expected_decoded_token = {
            "uid": "user_123",
            "email": "premium@example.com",
            "email_verified": True,
            "premium": True,
            "role": "admin",
        }

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.return_value = expected_decoded_token

            # Act
            mock_request = Mock()
            mock_request.state = Mock()
            result = await verify_firebase_token(mock_request, mock_credentials)

            # Assert
            assert result == expected_decoded_token
            assert result["premium"] is True
            assert result["role"] == "admin"


def _make_async_db(user):
    """Return an AsyncSession mock that yields *user* from execute().scalars().first()."""
    mock_scalars = Mock()
    mock_scalars.first.return_value = user
    mock_result = Mock()
    mock_result.scalars.return_value = mock_scalars
    mock_async_db = Mock()
    mock_async_db.execute = AsyncMock(return_value=mock_result)
    return mock_async_db


def _make_async_session_context(user):
    """Return an async session context manager mock that yields *user*."""
    mock_async_db = _make_async_db(user)
    mock_context = Mock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_async_db)
    mock_context.__aexit__ = AsyncMock(return_value=None)
    return mock_context, mock_async_db


class TestGetCurrentUserId:
    """Tests for get_current_user_id dependency."""

    @pytest.mark.asyncio
    async def test_get_user_id_success(self):
        """Test successful extraction of user ID from token."""
        # Arrange
        mock_user = Mock()
        mock_user.id = "user_db_id_123"
        mock_user.firebase_uid = "firebase_uid_123"

        mock_token = {
            "uid": "firebase_uid_123",
            "email": "test@example.com",
        }

        mock_async_db = _make_async_db(mock_user)

        # Act
        result = await resolve_current_user_id(mock_token, async_db=mock_async_db)

        # Assert
        assert result == "user_db_id_123"
        mock_async_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_user_id_missing_uid_in_token(self):
        """Test error when token is missing 'uid' field."""
        # Arrange
        mock_token = {
            "email": "test@example.com",
            # Missing 'uid' field
        }

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user_id(_auth_request(), mock_token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "missing user identifier" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_get_user_id_empty_uid_in_token(self):
        """Test error when token has empty 'uid' field."""
        # Arrange
        mock_token = {
            "uid": "",  # Empty uid
            "email": "test@example.com",
        }

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user_id(_auth_request(), mock_token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_get_user_id_user_not_found(self):
        """Test error when user with Firebase UID not found in database."""
        # Arrange
        mock_token = {
            "uid": "nonexistent_firebase_uid",
            "email": "nonexistent@example.com",
        }

        mock_async_db = _make_async_db(None)  # User not found

        with pytest.raises(HTTPException) as exc_info:
            await resolve_current_user_id(mock_token, async_db=mock_async_db)

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "detail" in dir(exc_info.value)
        detail = exc_info.value.detail
        assert detail["error_code"] == "USER_NOT_FOUND"
        assert "user not found or account has been deleted" in detail["message"].lower()
        assert detail["details"]["hint"] is not None
        assert "POST /v1/users/sync" in detail["details"]["hint"]

    @pytest.mark.asyncio
    async def test_get_user_id_multiple_users_same_firebase_uid(self):
        """Test that only first user is returned if duplicate Firebase UIDs exist."""
        # This tests the .first() behavior
        # Arrange
        mock_user1 = Mock()
        mock_user1.id = "user1_id"
        mock_user1.firebase_uid = "duplicate_firebase_uid"

        mock_token = {"uid": "duplicate_firebase_uid"}
        mock_async_db = _make_async_db(mock_user1)

        # Act
        result = await resolve_current_user_id(mock_token, async_db=mock_async_db)

        # Assert
        # Should return the first user's ID
        assert result == "user1_id"

    @pytest.mark.asyncio
    async def test_get_user_id_returns_cached_active_user(self):
        """Test cached Firebase UID mapping avoids a database lookup."""
        from src.api.dependencies import auth_cache as cache_module

        # Pre-populate the in-memory TTLCache directly
        cache_module._uid_cache["firebase_uid_123"] = {
            "user_id": "cached_user_id",
            "is_active": True,
        }
        try:
            mock_async_db = _make_async_db(None)
            mock_token = {"uid": "firebase_uid_123"}

            result = await resolve_current_user_id(
                mock_token, async_db=mock_async_db, cache_service=None
            )

            assert result == "cached_user_id"
            mock_async_db.execute.assert_not_awaited()
        finally:
            cache_module._uid_cache.pop("firebase_uid_123", None)

    @pytest.mark.asyncio
    async def test_get_user_id_caches_database_result_on_miss(self):
        """Test DB lookup result is cached in-memory after a DB hit."""
        from src.api.dependencies import auth_cache as cache_module

        mock_user = Mock()
        mock_user.id = "user_db_id_123"
        mock_user.firebase_uid = "firebase_uid_456"

        mock_async_db = _make_async_db(mock_user)

        # Ensure no stale entry
        cache_module._uid_cache.pop("firebase_uid_456", None)
        try:
            result = await resolve_current_user_id(
                {"uid": "firebase_uid_456"},
                async_db=mock_async_db,
                cache_service=None,
            )

            assert result == "user_db_id_123"
            # The in-memory cache should now hold the entry
            cached = cache_module._uid_cache.get("firebase_uid_456")
            assert cached is not None
            assert cached["user_id"] == "user_db_id_123"
        finally:
            cache_module._uid_cache.pop("firebase_uid_456", None)

    @pytest.mark.asyncio
    async def test_get_user_id_closes_internal_session_on_cache_miss(self):
        """Auth lookup should not hold a DB session for the full route lifetime."""
        from src.api.dependencies import auth_cache as cache_module

        mock_user = Mock()
        mock_user.id = "short_lived_user_id"
        firebase_uid = "firebase_uid_short_lived"
        mock_context, mock_async_db = _make_async_session_context(mock_user)

        cache_module._uid_cache.pop(firebase_uid, None)
        try:
            with patch(
                "src.api.dependencies.auth.AsyncSessionLocal",
                return_value=mock_context,
            ):
                result = await get_current_user_id(
                    _auth_request(),
                    {"uid": firebase_uid},
                    cache_service=None,
                )

            assert result == "short_lived_user_id"
            mock_async_db.execute.assert_awaited_once()
            mock_context.__aenter__.assert_awaited_once()
            mock_context.__aexit__.assert_awaited_once()
        finally:
            cache_module._uid_cache.pop(firebase_uid, None)


class TestGetCurrentUserEmail:
    """Tests for get_current_user_email dependency."""

    @pytest.mark.asyncio
    async def test_get_email_success(self):
        """Test successful extraction of email from token."""
        # Arrange
        mock_token = {
            "uid": "user_123",
            "email": "test@example.com",
        }

        # Act
        result = await get_current_user_email(mock_token)

        # Assert
        assert result == "test@example.com"

    @pytest.mark.asyncio
    async def test_get_email_missing_in_token(self):
        """Test returns None when email is missing from token."""
        # Arrange
        mock_token = {
            "uid": "user_123",
            # Missing 'email' field
        }

        # Act
        result = await get_current_user_email(mock_token)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_get_email_empty_in_token(self):
        """Test returns empty string when email is empty in token."""
        # Arrange
        mock_token = {
            "uid": "user_123",
            "email": "",
        }

        # Act
        result = await get_current_user_email(mock_token)

        # Assert
        assert result == ""

    @pytest.mark.asyncio
    async def test_get_email_none_in_token(self):
        """Test returns None when email is explicitly None in token."""
        # Arrange
        mock_token = {
            "uid": "user_123",
            "email": None,
        }

        # Act
        result = await get_current_user_email(mock_token)

        # Assert
        assert result is None


class TestOptionalAuthentication:
    """Tests for optional_authentication dependency."""

    @pytest.mark.asyncio
    async def test_optional_auth_no_credentials(self):
        """Test optional authentication returns None when no credentials provided."""
        # Arrange
        credentials = None

        # Act
        result = await optional_authentication(credentials)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_optional_auth_valid_credentials(self):
        """Test optional authentication returns decoded token for valid credentials."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "valid_token"

        expected_decoded_token = {
            "uid": "user_123",
            "email": "test@example.com",
        }

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.return_value = expected_decoded_token

            # Act
            result = await optional_authentication(mock_credentials)

            # Assert
            assert result == expected_decoded_token
            assert result["uid"] == "user_123"
            mock_verify.assert_called_once_with("valid_token")

    @pytest.mark.asyncio
    async def test_optional_auth_invalid_credentials(self):
        """Test optional authentication returns None for invalid credentials."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "invalid_token"

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.side_effect = firebase_auth.InvalidIdTokenError("Invalid token")

            # Act
            result = await optional_authentication(mock_credentials)

            # Assert
            assert result is None

    @pytest.mark.asyncio
    async def test_optional_auth_expired_credentials(self):
        """Test optional authentication returns None for expired credentials."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "expired_token"

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.side_effect = firebase_auth.ExpiredIdTokenError(
                "Token expired", cause=Exception("Expired")
            )

            # Act
            result = await optional_authentication(mock_credentials)

            # Assert
            assert result is None

    @pytest.mark.asyncio
    async def test_optional_auth_revoked_credentials(self):
        """Test optional authentication returns None for revoked credentials."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "revoked_token"

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.side_effect = firebase_auth.RevokedIdTokenError("Token revoked")

            # Act
            result = await optional_authentication(mock_credentials)

            # Assert
            assert result is None

    @pytest.mark.asyncio
    async def test_optional_auth_unexpected_error(self):
        """Test optional authentication returns None on unexpected error."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "some_token"

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.side_effect = Exception("Unexpected error")

            # Act
            result = await optional_authentication(mock_credentials)

            # Assert
            assert result is None


class TestAuthenticationIntegration:
    """Integration tests for authentication flow."""

    @pytest.mark.asyncio
    async def test_full_authentication_flow(self):
        """Test complete authentication flow from token to user ID."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "valid_firebase_token"

        mock_decoded_token = {
            "uid": "firebase_uid_123",
            "email": "test@example.com",
            "email_verified": True,
        }

        mock_user = Mock()
        mock_user.id = "user_db_id_123"
        mock_async_db = _make_async_db(mock_user)

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.return_value = mock_decoded_token

            # Act - Step 1: Verify token
            mock_request = Mock()
            token = await verify_firebase_token(mock_request, mock_credentials)

            # Assert - Token verified
            assert token["uid"] == "firebase_uid_123"
            assert token["email"] == "test@example.com"

            # Act - Step 2: Get user ID
            user_id = await resolve_current_user_id(token, async_db=mock_async_db)

            # Assert - User ID extracted
            assert user_id == "user_db_id_123"

            # Act - Step 3: Get email
            email = await get_current_user_email(token)

            # Assert - Email extracted
            assert email == "test@example.com"

    @pytest.mark.asyncio
    async def test_authentication_with_unverified_email(self):
        """Test authentication succeeds even with unverified email."""
        # Firebase allows unverified emails through, app logic decides what to do
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "token_unverified_email"

        mock_decoded_token = {
            "uid": "firebase_uid_123",
            "email": "test@example.com",
            "email_verified": False,  # Unverified
        }

        mock_user = Mock()
        mock_user.id = "user_db_id_123"
        mock_async_db = _make_async_db(mock_user)

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.return_value = mock_decoded_token

            # Act
            mock_request = Mock()
            mock_request.state = Mock()
            token = await verify_firebase_token(mock_request, mock_credentials)
            user_id = await resolve_current_user_id(token, async_db=mock_async_db)

            # Assert - Should still work
            assert token["email_verified"] is False
            assert user_id == "user_db_id_123"

    @pytest.mark.asyncio
    async def test_authentication_without_email_in_token(self):
        """Test authentication works even if token doesn't contain email."""
        # Some auth providers might not include email
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "token_no_email"

        mock_decoded_token = {
            "uid": "firebase_uid_123",
            # No email field
        }

        mock_user = Mock()
        mock_user.id = "user_db_id_123"
        mock_async_db = _make_async_db(mock_user)

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.return_value = mock_decoded_token

            # Act
            mock_request = Mock()
            mock_request.state = Mock()
            token = await verify_firebase_token(mock_request, mock_credentials)
            user_id = await resolve_current_user_id(token, async_db=mock_async_db)
            email = await get_current_user_email(token)

            # Assert
            assert user_id == "user_db_id_123"
            assert email is None


class TestAuthenticationEdgeCases:
    """Edge case tests for authentication."""

    @pytest.mark.asyncio
    async def test_token_with_special_characters_in_uid(self):
        """Test handling of Firebase UID with special characters."""
        # Arrange
        special_firebase_uid = "user@special_123-456.xyz"

        mock_user = Mock()
        mock_user.id = "special_user_id"
        mock_user.firebase_uid = special_firebase_uid
        mock_async_db = _make_async_db(mock_user)

        mock_token = {"uid": special_firebase_uid}

        # Act
        result = await resolve_current_user_id(mock_token, async_db=mock_async_db)

        # Assert
        assert result == "special_user_id"

    @pytest.mark.asyncio
    async def test_token_with_very_long_uid(self):
        """Test handling of Firebase UID that exceeds typical length."""
        # Arrange
        long_firebase_uid = "a" * 200  # Very long UID

        mock_user = Mock()
        mock_user.id = "long_user_id"
        mock_user.firebase_uid = long_firebase_uid
        mock_async_db = _make_async_db(mock_user)

        mock_token = {"uid": long_firebase_uid}

        # Act
        result = await resolve_current_user_id(mock_token, async_db=mock_async_db)

        # Assert
        assert result == "long_user_id"

    @pytest.mark.asyncio
    async def test_verify_token_preserves_all_claims(self):
        """Test that token verification preserves all custom claims."""
        # Arrange
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "complex_token"

        complex_decoded_token = {
            "uid": "user_123",
            "email": "user@example.com",
            "email_verified": True,
            "name": "Test User",
            "picture": "https://example.com/photo.jpg",
            "iss": "https://securetoken.google.com/project-id",
            "aud": "project-id",
            "auth_time": 1609459200,
            "user_id": "user_123",
            "sub": "user_123",
            "iat": 1609459200,
            "exp": 1609462800,
            "firebase": {
                "identities": {"email": ["user@example.com"]},
                "sign_in_provider": "password",
            },
            "custom_claim_1": "value1",
            "custom_claim_2": 42,
        }

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.return_value = complex_decoded_token

            # Act
            mock_request = Mock()
            mock_request.state = Mock()
            result = await verify_firebase_token(mock_request, mock_credentials)

            # Assert - All claims preserved
            assert result == complex_decoded_token
            assert result["custom_claim_1"] == "value1"
            assert result["custom_claim_2"] == 42
            assert "firebase" in result

    @pytest.mark.asyncio
    async def test_concurrent_token_verification(self):
        """Test that multiple concurrent token verifications work correctly."""
        # Arrange
        import asyncio

        mock_credentials1 = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials1.credentials = "token1"

        mock_credentials2 = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials2.credentials = "token2"

        mock_credentials3 = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials3.credentials = "token3"

        def mock_verify_side_effect(token):
            if token == "token1":
                return {"uid": "user1", "email": "user1@example.com"}
            elif token == "token2":
                return {"uid": "user2", "email": "user2@example.com"}
            elif token == "token3":
                return {"uid": "user3", "email": "user3@example.com"}

        with patch(
            "src.api.dependencies.auth.firebase_auth.verify_id_token"
        ) as mock_verify:
            mock_verify.side_effect = mock_verify_side_effect

            # Act - Verify multiple tokens concurrently
            mock_request1 = Mock()
            mock_request1.state = Mock()
            mock_request2 = Mock()
            mock_request2.state = Mock()
            mock_request3 = Mock()
            mock_request3.state = Mock()

            results = await asyncio.gather(
                verify_firebase_token(mock_request1, mock_credentials1),
                verify_firebase_token(mock_request2, mock_credentials2),
                verify_firebase_token(mock_request3, mock_credentials3),
            )

            # Assert
            assert len(results) == 3
            assert results[0]["uid"] == "user1"
            assert results[1]["uid"] == "user2"
            assert results[2]["uid"] == "user3"
            assert mock_verify.call_count == 3
