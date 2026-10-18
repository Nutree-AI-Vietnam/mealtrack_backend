"""Application-layer ports.

Infrastructure implements these contracts. The application layer depends on
the port, not on the adapter.
"""

from src.app.ports.image_download_port import ImageDownloadPort

__all__ = ["ImageDownloadPort"]
