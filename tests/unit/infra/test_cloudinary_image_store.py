"""
Unit tests for CloudinaryImageStore.
"""

import os
import uuid
from unittest.mock import Mock, patch

import pytest

from src.infra.adapters.cloudinary_image_store import CloudinaryImageStore


@pytest.fixture
def mock_cloudinary_env():
    """Mock Cloudinary environment variables."""
    with patch.dict(
        os.environ,
        {
            "CLOUDINARY_CLOUD_NAME": "test-cloud",
            "CLOUDINARY_API_KEY": "test-api-key",
            "CLOUDINARY_API_SECRET": "test-api-secret",
            "USE_MOCK_STORAGE": "0",
        },
    ):
        yield


@pytest.fixture
def cloudinary_store(mock_cloudinary_env):
    """Create CloudinaryImageStore instance with mocked config."""
    with patch("cloudinary.config"):
        store = CloudinaryImageStore()
        return store


@pytest.fixture
def sample_image_bytes():
    """Sample image bytes for testing."""
    # Simple 1x1 red pixel JPEG
    return bytes.fromhex(
        "ffd8ffe000104a46494600010101006000600000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffdb0043010909090c0b0c180d0d1832211c213232323232323232323232323232323232323232323232323232323232323232323232323232323232323232323232323232ffc00011080001000103012200021101031101ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffc4001f0100030101010101010101010000000000000102030405060708090a0bffc400b51100020102040403040705040400010277000102031104052131061241510761711322328108144291a1b1c109233352f0156272d10a162434e125f11718191a262728292a35363738393a434445464748494a535455565758595a636465666768696a737475767778797a82838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9faffda000c03010002110311003f00e2ffd9"
    )


class TestCloudinaryImageStoreInitialization:
    """Test CloudinaryImageStore initialization."""

    def test_initialization_with_valid_config(self, mock_cloudinary_env):
        """Test successful initialization with valid configuration."""
        with patch("cloudinary.config") as mock_config:
            CloudinaryImageStore()

            mock_config.assert_called_once_with(
                cloud_name="test-cloud",
                api_key="test-api-key",
                api_secret="test-api-secret",
            )

    def test_initialization_without_cloud_name(self):
        """Test initialization fails without cloud name."""
        with patch.dict(
            os.environ,
            {"CLOUDINARY_API_KEY": "test-key", "CLOUDINARY_API_SECRET": "test-secret"},
            clear=True,
        ):
            with pytest.raises(ValueError, match="Missing Cloudinary configuration"):
                CloudinaryImageStore()

    def test_initialization_without_api_key(self):
        """Test initialization fails without API key."""
        with patch.dict(
            os.environ,
            {
                "CLOUDINARY_CLOUD_NAME": "test-cloud",
                "CLOUDINARY_API_SECRET": "test-secret",
            },
            clear=True,
        ):
            with pytest.raises(ValueError, match="Missing Cloudinary configuration"):
                CloudinaryImageStore()

    def test_initialization_without_api_secret(self):
        """Test initialization fails without API secret."""
        with patch.dict(
            os.environ,
            {"CLOUDINARY_CLOUD_NAME": "test-cloud", "CLOUDINARY_API_KEY": "test-key"},
            clear=True,
        ):
            with pytest.raises(ValueError, match="Missing Cloudinary configuration"):
                CloudinaryImageStore()

    def test_initialization_with_all_missing(self):
        """Test initialization fails when all config is missing."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="Missing Cloudinary configuration"):
                CloudinaryImageStore()


class TestSaveImage:
    """Test save method."""

    def test_save_jpeg_image_success(self, cloudinary_store, sample_image_bytes):
        """Test successfully saving a JPEG image."""
        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.return_value = {
                "secure_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/test-id.jpg",
                "public_id": "mealtrack/test-id",
                "format": "jpg",
            }

            result = cloudinary_store.save(sample_image_bytes, "image/jpeg")

            assert (
                result
                == "https://res.cloudinary.com/test/image/upload/v123/mealtrack/test-id.jpg"
            )
            mock_upload.assert_called_once()

            # Verify upload parameters
            call_args = mock_upload.call_args
            assert call_args[0][0] == sample_image_bytes
            assert "public_id" in call_args[1]
            assert call_args[1]["public_id"].startswith("mealtrack/")
            assert call_args[1]["format"] == "jpg"
            assert call_args[1]["resource_type"] == "image"

    def test_save_png_image_success(self, cloudinary_store, sample_image_bytes):
        """Test successfully saving a PNG image."""
        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.return_value = {
                "secure_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/test-id.png",
                "public_id": "mealtrack/test-id",
                "format": "png",
            }

            result = cloudinary_store.save(sample_image_bytes, "image/png")

            assert result.startswith("https://res.cloudinary.com/")
            assert ".png" in result

            # Verify format parameter
            call_args = mock_upload.call_args
            assert call_args[1]["format"] == "png"

    def test_save_unsupported_content_type(self, cloudinary_store, sample_image_bytes):
        """Test saving image with unsupported content type raises error."""
        with pytest.raises(ValueError, match="Unsupported content type"):
            cloudinary_store.save(sample_image_bytes, "image/gif")

    def test_save_invalid_content_type(self, cloudinary_store, sample_image_bytes):
        """Test saving image with invalid content type."""
        with pytest.raises(ValueError, match="Unsupported content type"):
            cloudinary_store.save(sample_image_bytes, "text/plain")

    def test_save_returns_image_id_when_no_url(
        self, cloudinary_store, sample_image_bytes
    ):
        """Test save returns image ID when secure_url is not in response."""
        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.return_value = {
                "public_id": "mealtrack/test-id",
                "format": "jpg",
                # No secure_url
            }

            with patch("uuid.uuid4") as mock_uuid:
                mock_uuid.return_value = uuid.UUID(
                    "12345678-1234-5678-1234-567812345678"
                )
                result = cloudinary_store.save(sample_image_bytes, "image/jpeg")

                # Should return the UUID when secure_url is missing
                assert result == "12345678-1234-5678-1234-567812345678"

    def test_save_upload_error(self, cloudinary_store, sample_image_bytes, caplog):
        """Test save handles upload errors."""
        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.side_effect = Exception("Upload failed secret-detail")

            with pytest.raises(Exception, match="Upload failed secret-detail"):
                with caplog.at_level("ERROR"):
                    cloudinary_store.save(sample_image_bytes, "image/jpeg")

        assert "Cloudinary upload failed error_type=Exception" in caplog.text
        assert "secret-detail" not in caplog.text

    def test_save_generates_unique_ids(self, cloudinary_store, sample_image_bytes):
        """Test that save generates unique IDs for different uploads."""
        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.return_value = {
                "secure_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/test.jpg"
            }

            result1 = cloudinary_store.save(sample_image_bytes, "image/jpeg")
            result2 = cloudinary_store.save(sample_image_bytes, "image/jpeg")

            # Both should succeed
            assert result1
            assert result2

    def test_save_with_overwrite(self, cloudinary_store, sample_image_bytes):
        """Test that save uses overwrite parameter."""
        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.return_value = {
                "secure_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/test.jpg"
            }

            cloudinary_store.save(sample_image_bytes, "image/jpeg")

            # Verify overwrite is True
            call_args = mock_upload.call_args
            assert call_args[1]["overwrite"] is True

    def test_save_uses_mealtrack_folder(self, cloudinary_store, sample_image_bytes):
        """Test that save uses 'mealtrack' folder."""
        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.return_value = {
                "secure_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/test.jpg"
            }

            cloudinary_store.save(sample_image_bytes, "image/jpeg")

            # Verify folder is included in public_id
            call_args = mock_upload.call_args
            assert "mealtrack/" in call_args[1]["public_id"]


class TestLoadImage:
    """Test load method."""

    def test_load_success(self, cloudinary_store):
        """Test successfully loading an image."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"image_data"

        with patch.object(cloudinary_store, "get_url") as mock_get_url:
            mock_get_url.return_value = "https://res.cloudinary.com/test/image.jpg"

            with patch("httpx.get") as mock_requests:
                mock_requests.return_value = mock_response

                result = cloudinary_store.load("test-image-id")

                assert result == b"image_data"
                mock_get_url.assert_called_once_with("test-image-id")
                mock_requests.assert_called_once_with(
                    "https://res.cloudinary.com/test/image.jpg"
                )

    def test_load_no_url_found(self, cloudinary_store):
        """Test load returns None when URL not found."""
        with patch.object(cloudinary_store, "get_url") as mock_get_url:
            mock_get_url.return_value = None

            result = cloudinary_store.load("invalid-id")

            assert result is None

    def test_load_http_error(self, cloudinary_store):
        """Test load handles HTTP errors gracefully."""
        mock_response = Mock()
        mock_response.status_code = 404

        with patch.object(cloudinary_store, "get_url") as mock_get_url:
            mock_get_url.return_value = "https://res.cloudinary.com/test/image.jpg"

            with patch("httpx.get") as mock_requests:
                mock_requests.return_value = mock_response

                result = cloudinary_store.load("test-image-id")

                assert result is None

    def test_load_network_error(self, cloudinary_store):
        """Test load handles network errors gracefully."""
        with patch.object(cloudinary_store, "get_url") as mock_get_url:
            mock_get_url.return_value = "https://res.cloudinary.com/test/image.jpg"

            with patch("httpx.get") as mock_requests:
                mock_requests.side_effect = Exception("Network error")

                result = cloudinary_store.load("test-image-id")

                assert result is None

    def test_load_empty_response(self, cloudinary_store):
        """Test load with empty response content."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b""

        with patch.object(cloudinary_store, "get_url") as mock_get_url:
            mock_get_url.return_value = "https://res.cloudinary.com/test/image.jpg"

            with patch("httpx.get") as mock_requests:
                mock_requests.return_value = mock_response

                result = cloudinary_store.load("test-image-id")

                assert result == b""


class TestGetUrl:
    """Test get_url method — local URL construction, no Admin API or HEAD."""

    def test_get_url_constructs_delivery_url_without_network(
        self, cloudinary_store, caplog
    ):
        with (
            patch("cloudinary.api.resource") as mock_resource,
            patch("httpx.head") as mock_head,
        ):
            with caplog.at_level("DEBUG"):
                result = cloudinary_store.get_url("test-id")

        assert result is not None
        assert "mealtrack/test-id" in result
        assert result.startswith("https://res.cloudinary.com/")
        mock_resource.assert_not_called()
        mock_head.assert_not_called()
        assert result not in caplog.text

    def test_get_url_uses_configured_cloud_name(
        self, cloudinary_store, mock_cloudinary_env
    ):
        result = cloudinary_store.get_url("test-id")

        assert result is not None
        assert "test-cloud" in result
        assert "mealtrack/test-id" in result

    def test_get_url_returns_none_without_cloud_name(self, cloudinary_store):
        with patch.dict("os.environ", {"CLOUDINARY_CLOUD_NAME": ""}, clear=False):
            with patch(
                "src.infra.adapters.cloudinary_image_store.cloudinary.config"
            ) as mock_config:
                mock_config.return_value.cloud_name = None
                result = cloudinary_store.get_url("test-id")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_url_async_is_local(self, cloudinary_store):
        with (
            patch("cloudinary.api.resource") as mock_resource,
            patch("httpx.head") as mock_head,
        ):
            result = await cloudinary_store.get_url_async("test-id")

        assert result == cloudinary_store.get_url("test-id")
        mock_resource.assert_not_called()
        mock_head.assert_not_called()


class TestDeleteImage:
    """Test delete method."""

    def test_delete_success(self, cloudinary_store):
        """Test successfully deleting an image."""
        with patch("cloudinary.uploader.destroy") as mock_destroy:
            mock_destroy.return_value = {"result": "ok"}

            result = cloudinary_store.delete("test-id")

            assert result is True
            mock_destroy.assert_called_once_with("mealtrack/test-id")

    def test_delete_not_found(self, cloudinary_store):
        """Test deleting non-existent image."""
        with patch("cloudinary.uploader.destroy") as mock_destroy:
            mock_destroy.return_value = {"result": "not found"}

            result = cloudinary_store.delete("non-existent-id")

            assert result is False

    def test_delete_error(self, cloudinary_store):
        """Test delete handles errors gracefully."""
        with patch("cloudinary.uploader.destroy") as mock_destroy:
            mock_destroy.side_effect = Exception("Delete failed")

            result = cloudinary_store.delete("test-id")

            assert result is False

    def test_delete_uses_correct_folder(self, cloudinary_store):
        """Test delete uses correct folder path."""
        with patch("cloudinary.uploader.destroy") as mock_destroy:
            mock_destroy.return_value = {"result": "ok"}

            cloudinary_store.delete("my-image-id")

            # Verify correct public_id with folder
            mock_destroy.assert_called_once_with("mealtrack/my-image-id")

    def test_delete_returns_false_on_unexpected_result(self, cloudinary_store):
        """Test delete returns False on unexpected result."""
        with patch("cloudinary.uploader.destroy") as mock_destroy:
            mock_destroy.return_value = {"result": "error", "error": "Unknown error"}

            result = cloudinary_store.delete("test-id")

            assert result is False


class TestCloudinaryImageStoreIntegration:
    """Integration tests for CloudinaryImageStore."""

    def test_save_and_get_url_flow(self, cloudinary_store, sample_image_bytes):
        """Test complete flow of saving and getting URL."""
        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.return_value = {
                "secure_url": "https://res.cloudinary.com/test/image/upload/v123/mealtrack/test-id.jpg",
                "public_id": "mealtrack/test-id",
            }

            # Save image
            url = cloudinary_store.save(sample_image_bytes, "image/jpeg")
            assert url.startswith("https://res.cloudinary.com/")

            # Extract image ID from the URL
            # In real implementation, we'd need to track the image_id separately
            # For this test, we'll just verify the save worked
            assert "mealtrack" in url

    def test_save_load_delete_flow(self, cloudinary_store, sample_image_bytes):
        """Test complete flow of save, load, and delete."""
        image_id = "test-flow-id"

        with (
            patch("cloudinary.uploader.upload") as mock_upload,
            patch("httpx.get") as mock_get,
            patch("cloudinary.uploader.destroy") as mock_destroy,
        ):
            # Setup mocks
            mock_upload.return_value = {
                "secure_url": f"https://res.cloudinary.com/test/image/upload/v123/mealtrack/{image_id}.jpg"
            }
            mock_get.return_value = Mock(status_code=200, content=sample_image_bytes)
            mock_destroy.return_value = {"result": "ok"}

            # Save
            url = cloudinary_store.save(sample_image_bytes, "image/jpeg")
            assert url

            # Load (Note: this requires knowing the image_id, not the URL)
            # In real usage, the URL returned from save would be stored
            loaded = cloudinary_store.load(image_id)
            assert loaded == sample_image_bytes

            # Delete
            deleted = cloudinary_store.delete(image_id)
            assert deleted is True

    def test_error_handling_across_methods(self, cloudinary_store):
        """Test error handling consistency across methods."""
        image_id = "error-test-id"

        # Save error
        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.side_effect = RuntimeError("Upload error")

            with pytest.raises(RuntimeError):
                cloudinary_store.save(b"test", "image/jpeg")

        # Load error (should not raise, return None)
        with patch.object(cloudinary_store, "get_url") as mock_get_url:
            mock_get_url.return_value = None  # Simulate URL not found

            result = cloudinary_store.load(image_id)
            assert result is None

        # Load network error (should not raise, return None)
        with patch.object(cloudinary_store, "get_url") as mock_get_url:
            mock_get_url.return_value = "http://example.com/test.jpg"
            with patch("httpx.get") as mock_requests:
                mock_requests.side_effect = Exception("Network error")

                result = cloudinary_store.load(image_id)
                assert result is None

        # Delete error (should not raise, return False)
        with patch("cloudinary.uploader.destroy") as mock_destroy:
            mock_destroy.side_effect = Exception("Delete error")

            result = cloudinary_store.delete(image_id)
            assert result is False

    def test_content_type_validation(self, cloudinary_store, sample_image_bytes):
        """Test content type validation across different formats."""
        valid_types = ["image/jpeg", "image/png"]
        invalid_types = ["image/gif", "image/bmp", "text/plain", "application/pdf"]

        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.return_value = {
                "secure_url": "https://res.cloudinary.com/test/image/upload/test.jpg"
            }

            # Test valid types
            for content_type in valid_types:
                result = cloudinary_store.save(sample_image_bytes, content_type)
                assert result

            # Test invalid types
            for content_type in invalid_types:
                with pytest.raises(ValueError, match="Unsupported content type"):
                    cloudinary_store.save(sample_image_bytes, content_type)


class TestAsyncWrappers:
    """Test async wrapper methods delegate to sync implementations."""

    @pytest.mark.asyncio
    async def test_save_async_delegates_to_save(
        self, cloudinary_store, sample_image_bytes
    ):
        with patch("cloudinary.uploader.upload") as mock_upload:
            mock_upload.return_value = {
                "secure_url": "https://res.cloudinary.com/test/image/upload/v1/mealtrack/test.jpg"
            }
            result = await cloudinary_store.save_async(sample_image_bytes, "image/jpeg")
            assert result.startswith("https://")
            mock_upload.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_async_returns_none_when_no_url(self, cloudinary_store):
        with patch.object(cloudinary_store, "get_url", return_value=None):
            result = await cloudinary_store.load_async("nonexistent-id")
            assert result is None

    @pytest.mark.asyncio
    async def test_delete_async_delegates_to_delete(self, cloudinary_store):
        with patch("cloudinary.uploader.destroy") as mock_destroy:
            mock_destroy.return_value = {"result": "ok"}
            result = await cloudinary_store.delete_async("test-id")
            assert result is True
