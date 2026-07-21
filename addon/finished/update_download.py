"""Bounded download and ZIP verification for a validated Finished? update release."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import tomllib
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from . import update_checker
from . import state_paths
from .release_metadata import ReleaseMetadata, ReleaseMetadataError, compare_versions


MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_ZIP_MEMBERS = 200
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
CACHE_DIRECTORY_NAME = "updates"
PACKAGE_IDENTITY_NAME = "release_identity.json"
MANIFEST_NAME = "blender_manifest.toml"
EXPECTED_EXTENSION_ID = "finished"
MAX_PACKAGE_REDIRECTS = 3
GITHUB_RELEASE_ASSET_HOSTS = frozenset(
    {
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)


@dataclass(frozen=True)
class PackagePreparationResult:
    prepared: bool
    path: Path | None = None
    error: str = ""


def default_cache_directory():
    return state_paths.state_directory() / CACHE_DIRECTORY_NAME


def discard_interrupted_downloads(cache_directory=None):
    """Remove only incomplete or unclaimed update downloads from an earlier Blender run."""

    cache_directory = Path(cache_directory or default_cache_directory())
    try:
        for path in cache_directory.glob("finished-update-*.part"):
            _remove_file(path)
        for path in cache_directory.glob("finished-update-*.zip"):
            _remove_file(path)
    except OSError:
        pass


def download_and_verify(metadata: ReleaseMetadata, blender_version: str, cache_directory=None, opener=None):
    """Download one package to a temporary file, verify it, then atomically prepare it."""

    if not update_checker.is_allowed_package_url(metadata.download_url):
        return PackagePreparationResult(False, error="download_url_not_allowed")

    cache_directory = Path(cache_directory or default_cache_directory())
    temporary_path = None
    try:
        cache_directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="finished-update-", suffix=".part", dir=cache_directory
        )
        temporary_path = Path(temporary_name)
        opener = opener or build_opener(_GitHubReleaseRedirectHandler())
        request = Request(
            metadata.download_url,
            headers={"Accept": "application/zip", "User-Agent": "Finished?-addon-update-download"},
            method="GET",
        )
        with os.fdopen(descriptor, "wb") as destination, opener.open(request, timeout=10.0) as response:
            status_code = response.getcode()
            if not 200 <= status_code < 300:
                return PackagePreparationResult(False, error="http_error")
            digest = hashlib.sha256()
            total_bytes = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_PACKAGE_BYTES:
                    return PackagePreparationResult(False, error="package_too_large")
                destination.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != metadata.sha256:
            return PackagePreparationResult(False, error="sha256_mismatch")
        error = verify_package(temporary_path, metadata, blender_version)
        if error:
            return PackagePreparationResult(False, error=error)
        prepared_path = cache_directory / f"finished-update-{metadata.version}.zip"
        os.replace(temporary_path, prepared_path)
        temporary_path = None
        return PackagePreparationResult(True, path=prepared_path)
    except (HTTPError, URLError, OSError):
        return PackagePreparationResult(False, error="download_failed")
    finally:
        if temporary_path is not None:
            _remove_file(temporary_path)


def verify_package(package_path, metadata: ReleaseMetadata, blender_version: str):
    """Return a safe rejection code, or an empty string when a ZIP matches the release contract."""

    try:
        with zipfile.ZipFile(package_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                return "package_too_complex"
            if any(_unsafe_zip_name(member.filename) for member in members):
                return "unsafe_package_path"
            if sum(member.file_size for member in members) > MAX_UNCOMPRESSED_BYTES:
                return "package_too_large"
            manifest = tomllib.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            identity = _read_package_identity(archive)
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, zipfile.BadZipFile):
        return "invalid_package"
    except OSError:
        return "invalid_package"

    if manifest.get("id") != EXPECTED_EXTENSION_ID:
        return "wrong_extension_id"
    if identity.get("extension_id") != EXPECTED_EXTENSION_ID:
        return "wrong_extension_id"
    if identity.get("channel") != metadata.channel:
        return "wrong_channel"
    if manifest.get("version") != metadata.version:
        return "wrong_package_version"

    manifest_minimum = manifest.get("blender_version_min")
    try:
        compare_versions(manifest_minimum, "0.0.0")
        if metadata.min_blender_version and manifest_minimum != metadata.min_blender_version:
            return "wrong_blender_version"
        if compare_versions(blender_version, manifest_minimum) < 0:
            return "blender_incompatible"
        if metadata.min_blender_version and compare_versions(
            blender_version, metadata.min_blender_version
        ) < 0:
            return "blender_incompatible"
    except ReleaseMetadataError:
        return "wrong_blender_version"
    return ""


def _read_package_identity(archive):
    identity = json.loads(archive.read(PACKAGE_IDENTITY_NAME).decode("utf-8"))
    if not isinstance(identity, dict) or set(identity) != {"extension_id", "channel"}:
        raise json.JSONDecodeError("invalid package identity", "", 0)
    if not all(isinstance(value, str) and value for value in identity.values()):
        raise json.JSONDecodeError("invalid package identity", "", 0)
    return identity


def _unsafe_zip_name(name):
    path = Path(name)
    return path.is_absolute() or ".." in path.parts or "\\" in name


def _is_allowed_github_release_asset_url(url):
    """Accept only HTTPS redirects to GitHub's release asset hosts."""

    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in GITHUB_RELEASE_ASSET_HOSTS
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
    )


class _GitHubReleaseRedirectHandler(HTTPRedirectHandler):
    """Permit GitHub Releases' signed CDN hand-off, but no arbitrary redirects."""

    def __init__(self):
        super().__init__()
        self._redirect_count = 0

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        self._redirect_count += 1
        if (
            self._redirect_count > MAX_PACKAGE_REDIRECTS
            or not _is_allowed_github_release_asset_url(newurl)
        ):
            raise URLError("package redirect is not allowed")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _remove_file(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
