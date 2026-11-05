"""Store SQLite snapshots in the existing GitHub repository.

Copies live on the side branch as release assets. The pointer is a small
JSON file on that same branch. Updating it sends the current file SHA, so
GitHub rejects a write that would cover a newer pointer. No new account and
no paid storage are involved. Tokens are never written to logs.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Callable

from db.durable_sqlite import DurableConflict, Pointer, SnapshotStore

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
POINTER_PATH = "durable-pointer.json"
RELEASE_TAG = "durable-snapshot"
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class GitHubSnapshotStore(SnapshotStore):
    def __init__(
        self,
        repo: str,
        token: str,
        branch: str = "durable-snapshot",
        transport: Transport | None = None,
    ) -> None:
        self.repo = repo.strip().strip("/")
        self._token = token.strip()
        self.branch = branch.strip() or "durable-snapshot"
        self._transport = transport or _urlopen_transport
        self._release_id: int | None = None
        self._is_private: bool | None = None

    def ensure_private(self) -> None:
        if self._is_private is True:
            return
        status, raw = self._request("GET", f"/repos/{self.repo}")
        if status != 200:
            raise DurableConflict(f"GitHub repository lookup failed with HTTP {status}")
        payload = json.loads(raw.decode("utf-8"))
        if not bool(payload.get("private")):
            raise DurableConflict("repository is public")
        self._is_private = True

    def read_pointer(self) -> Pointer | None:
        self.ensure_private()
        status, body = self._request(
            "GET",
            f"/repos/{self.repo}/contents/{POINTER_PATH}?ref={self.branch}",
        )
        if status == 404:
            return None
        if status != 200:
            raise DurableConflict(f"GitHub pointer read failed with HTTP {status}")
        payload = json.loads(body.decode("utf-8"))
        encoded = payload.get("content") or ""
        decoded = base64.b64decode("".join(str(encoded).split()))
        data = json.loads(decoded.decode("utf-8"))
        return Pointer(
            version=int(data.get("version") or 0),
            sha256=str(data.get("sha256") or ""),
            asset_name=str(data.get("asset_name") or ""),
            lease_holder=str(data.get("lease_holder") or ""),
            lease_until=str(data.get("lease_until") or ""),
            content_sha=str(payload.get("sha") or "") or None,
        )

    def compare_and_swap(self, pointer: Pointer, expected_content_sha: str | None) -> Pointer:
        self.ensure_private()
        self._ensure_branch()
        document = {
            "version": pointer.version,
            "sha256": pointer.sha256,
            "asset_name": pointer.asset_name,
            "lease_holder": pointer.lease_holder,
            "lease_until": pointer.lease_until,
        }
        body: dict[str, object] = {
            "message": f"Publish SQLite snapshot {pointer.version}",
            "content": base64.b64encode(
                json.dumps(document, separators=(",", ":")).encode("utf-8")
            ).decode("ascii"),
            "branch": self.branch,
        }
        if expected_content_sha:
            body["sha"] = expected_content_sha
        status, raw = self._request(
            "PUT",
            f"/repos/{self.repo}/contents/{POINTER_PATH}",
            body,
        )
        if status == 409:
            raise DurableConflict("GitHub rejected the pointer update")
        if status not in {200, 201}:
            raise DurableConflict(f"GitHub pointer write failed with HTTP {status}")
        payload = json.loads(raw.decode("utf-8"))
        content = payload.get("content") or {}
        return Pointer(
            version=pointer.version,
            sha256=pointer.sha256,
            asset_name=pointer.asset_name,
            lease_holder=pointer.lease_holder,
            lease_until=pointer.lease_until,
            content_sha=str(content.get("sha") or "") or None,
        )

    def upload(self, asset_name: str, data: bytes) -> None:
        self.ensure_private()
        release_id = self._release()
        status, _raw = self._request(
            "POST",
            f"/repos/{self.repo}/releases/{release_id}/assets?name={asset_name}",
            raw_body=data,
            uploads=True,
        )
        if status == 422:
            return
        if status not in {201, 200}:
            raise DurableConflict(f"GitHub snapshot upload failed with HTTP {status}")

    def download(self, asset_name: str) -> bytes:
        release_id = self._release()
        status, raw = self._request(
            "GET",
            f"/repos/{self.repo}/releases/{release_id}/assets",
        )
        if status != 200:
            raise DurableConflict(f"GitHub asset list failed with HTTP {status}")
        assets = json.loads(raw.decode("utf-8"))
        match = next(
            (item for item in assets if item.get("name") == asset_name),
            None,
        )
        if match is None:
            raise DurableConflict("published snapshot asset is missing")
        asset_id = int(match["id"])
        status, payload = self._request(
            "GET",
            f"/repos/{self.repo}/releases/assets/{asset_id}",
            accept="application/octet-stream",
        )
        if status != 200:
            raise DurableConflict(f"GitHub snapshot download failed with HTTP {status}")
        return payload

    def prune(self, keep: set[str]) -> None:
        if self._release_id is None:
            return
        status, raw = self._request(
            "GET",
            f"/repos/{self.repo}/releases/{self._release_id}/assets",
        )
        if status != 200:
            return
        for item in json.loads(raw.decode("utf-8")):
            name = str(item.get("name") or "")
            if not name or name in keep:
                continue
            self._request(
                "DELETE",
                f"/repos/{self.repo}/releases/assets/{int(item['id'])}",
            )

    def _release(self) -> int:
        if self._release_id is not None:
            return self._release_id
        self._ensure_branch()
        status, raw = self._request(
            "GET",
            f"/repos/{self.repo}/releases/tags/{RELEASE_TAG}",
        )
        if status == 404:
            status, raw = self._request(
                "POST",
                f"/repos/{self.repo}/releases",
                {
                    "tag_name": RELEASE_TAG,
                    "target_commitish": self.branch,
                    "name": "SQLite snapshots",
                    "prerelease": True,
                },
            )
        if status not in {200, 201}:
            raise DurableConflict(f"GitHub release lookup failed with HTTP {status}")
        self._release_id = int(json.loads(raw.decode("utf-8"))["id"])
        return self._release_id

    def _ensure_branch(self) -> None:
        status, _raw = self._request(
            "GET",
            f"/repos/{self.repo}/git/ref/heads/{self.branch}",
        )
        if status == 200:
            return
        if status != 404:
            raise DurableConflict(f"GitHub branch lookup failed with HTTP {status}")
        status, raw = self._request("GET", f"/repos/{self.repo}/git/ref/heads/main")
        if status != 200:
            raise DurableConflict(f"GitHub main lookup failed with HTTP {status}")
        sha = str(json.loads(raw.decode("utf-8"))["object"]["sha"])
        status, _created = self._request(
            "POST",
            f"/repos/{self.repo}/git/refs",
            {"ref": f"refs/heads/{self.branch}", "sha": sha},
        )
        if status not in {201, 422}:
            raise DurableConflict(f"GitHub branch create failed with HTTP {status}")

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        raw_body: bytes | None = None,
        uploads: bool = False,
        accept: str = "application/vnd.github+json",
    ) -> tuple[int, bytes]:
        host = UPLOADS if uploads else API
        url = host + path
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "meta-dashboard-snapshot",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        data = raw_body
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif raw_body is not None:
            headers["Content-Type"] = "application/octet-stream"
        try:
            return self._transport(method, url, headers, data)
        except Exception as exc:
            raise DurableConflict(f"GitHub request failed: {type(exc).__name__}") from exc


def _urlopen_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    data: bytes | None,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        return int(exc.code), payload
