"""Binary store — CRUD for file metadata and disk operations.

Manages the ``binary_assets`` table and on-disk file storage under
``data/workspaces/{user_id}/{workspace}/``.

SHA-256 content hashing provides deduplication: identical uploads share
a single disk write.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import Any, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# Default limits
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
ALLOWED_MIME_PREFIXES = (
    "text/", "application/json", "application/pdf", "application/xml",
    "application/zip", "application/gzip", "application/x-tar",
    "image/", "audio/", "video/",
    "application/octet-stream",
    "application/javascript", "application/yaml",
    "application/vnd.openxmlformats",
)


def sha256_hash(data: bytes) -> str:
    """Return hex-encoded SHA-256 of *data*."""
    return hashlib.sha256(data).hexdigest()


class BinaryStore:
    """File metadata repository (DB) + on-disk storage."""

    def __init__(self, pool: "asyncpg.Pool", base_dir: str | Path) -> None:
        self._pool = pool
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Storage path helpers
    # ------------------------------------------------------------------

    def _user_dir(self, user_id: str, workspace: str = "default") -> Path:
        p = self._base / user_id / workspace
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _file_path(self, user_id: str, workspace: str, filename: str) -> Path:
        return self._user_dir(user_id, workspace) / filename

    # ------------------------------------------------------------------
    # Upload (with SHA-256 dedup)
    # ------------------------------------------------------------------

    async def save(
        self,
        *,
        user_id: str,
        workspace: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        """Save file to disk and record metadata in ``binary_assets``.

        Returns a dict with ``id``, ``sha256``, ``size``, ``path``, ``deduplicated``.
        """
        content_hash = sha256_hash(data)
        size = len(data)
        dest = self._file_path(user_id, workspace, filename)

        # Check deduplication: same hash already on disk?
        deduplicated = False
        existing = await self._pool.fetchrow(
            "SELECT id, disk_path FROM dbo.binary_assets WHERE sha256_hash = $1",
            content_hash,
        )
        if existing and Path(existing["disk_path"]).exists():
            deduplicated = True
            # Still record a new DB row pointing to same content
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        asset_id = str(uuid4())
        disk_path = str(dest) if not deduplicated else existing["disk_path"]

        await self._pool.execute(
            """
            INSERT INTO dbo.binary_assets (id, owner_id, filename, content_type,
                                           sha256_hash, size_bytes, disk_path)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            asset_id, user_id, filename, content_type, content_hash, size, disk_path,
        )

        return {
            "id": asset_id,
            "sha256": content_hash,
            "size": size,
            "path": disk_path,
            "deduplicated": deduplicated,
        }

    # ------------------------------------------------------------------
    # Download / lookup
    # ------------------------------------------------------------------

    async def get_metadata(self, asset_id: str) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM dbo.binary_assets WHERE id = $1", asset_id,
        )
        return dict(row) if row else None

    async def get_disk_path(self, asset_id: str) -> Path | None:
        row = await self._pool.fetchrow(
            "SELECT disk_path FROM dbo.binary_assets WHERE id = $1", asset_id,
        )
        if row:
            p = Path(row["disk_path"])
            return p if p.exists() else None
        return None

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete(self, asset_id: str) -> bool:
        """Delete metadata. Disk file removed only if no other rows share hash."""
        row = await self._pool.fetchrow(
            "SELECT sha256_hash, disk_path FROM dbo.binary_assets WHERE id = $1",
            asset_id,
        )
        if not row:
            return False

        await self._pool.execute("DELETE FROM dbo.binary_assets WHERE id = $1", asset_id)

        # Check whether other rows reference the same hash
        count = await self._pool.fetchval(
            "SELECT count(*) FROM dbo.binary_assets WHERE sha256_hash = $1",
            row["sha256_hash"],
        )
        if count == 0:
            p = Path(row["disk_path"])
            if p.exists():
                p.unlink()
        return True

    # ------------------------------------------------------------------
    # Workspace browsing
    # ------------------------------------------------------------------

    async def list_files(self, user_id: str, workspace: str = "default") -> list[dict[str, Any]]:
        """List files in a user's workspace (DB records)."""
        rows = await self._pool.fetch(
            """
            SELECT id, filename, content_type, size_bytes, sha256_hash, created_at
            FROM dbo.binary_assets
            WHERE owner_id = $1
            ORDER BY created_at DESC
            """,
            user_id,
        )
        return [dict(r) for r in rows]

    def browse_workspace(self, user_id: str, workspace: str = "default") -> list[dict[str, Any]]:
        """Walk the workspace directory tree and return a JSON-friendly listing."""
        root = self._user_dir(user_id, workspace)
        result: list[dict[str, Any]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            rel = Path(dirpath).relative_to(root)
            for fn in sorted(filenames):
                fp = Path(dirpath) / fn
                result.append({
                    "name": fn,
                    "path": str(rel / fn),
                    "size": fp.stat().st_size,
                    "is_dir": False,
                })
            for dn in sorted(dirnames):
                result.append({
                    "name": dn,
                    "path": str(rel / dn),
                    "is_dir": True,
                })
        return result
