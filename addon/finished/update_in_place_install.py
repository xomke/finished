"""Install a verified update beside the running add-on, for the next Blender launch.

The running Python modules are intentionally never reloaded.  This mirrors the
well-established BlenderKit pattern: replace files on disk, then let a normal
Blender restart load the new package.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import uuid
import zipfile


MAX_ARCHIVE_MEMBER_BYTES = 100 * 1024 * 1024
BACKUP_PREFIX = ".finished-update-backup-"


@dataclass(frozen=True)
class InPlaceInstallResult:
    installed: bool
    error: str = ""
    backup_path: Path | None = None


def install_prepared_package(package_path, *, addon_directory=None):
    """Replace the add-on directory from a verified ZIP without reloading it.

    Both the staging directory and backup live beside the target so the two
    directory renames stay on one filesystem.  If the second rename fails, the
    original directory is restored before this function returns.
    """

    package = Path(package_path)
    target = Path(addon_directory or Path(__file__).parent)
    if not package.is_absolute() or not package.is_file() or package.suffix.lower() != ".zip":
        return InPlaceInstallResult(False, "prepared_package_missing")
    if not target.is_dir() or not (target / "blender_manifest.toml").is_file():
        return InPlaceInstallResult(False, "addon_directory_unavailable")

    token = uuid.uuid4().hex
    staging = target.parent / f".{target.name}-update-staging-{token}"
    backup = target.parent / f"{BACKUP_PREFIX}{token}"
    try:
        _extract_package(package, staging)
        if not (staging / "blender_manifest.toml").is_file() or not (staging / "__init__.py").is_file():
            return InPlaceInstallResult(False, "invalid_package")
        _remove_old_backups(target.parent)
        os.replace(target, backup)
        try:
            os.replace(staging, target)
        except OSError:
            os.replace(backup, target)
            return InPlaceInstallResult(False, "install_failed")
        return InPlaceInstallResult(True, backup_path=backup)
    except (OSError, zipfile.BadZipFile):
        return InPlaceInstallResult(False, "install_failed")
    finally:
        _remove_tree(staging)


def _extract_package(package, staging):
    with zipfile.ZipFile(package) as archive:
        members = archive.infolist()
        if not members or any(not _safe_member(member) for member in members):
            raise zipfile.BadZipFile("unsafe update package")
        staging.mkdir()
        for member in members:
            destination = staging / member.filename
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=64 * 1024)


def _safe_member(member):
    path = Path(member.filename)
    # ZIP external attributes can encode a Unix symlink.  Never unpack one into
    # an extension directory, even when its textual path itself is safe.
    mode = member.external_attr >> 16
    is_symlink = (mode & 0o170000) == 0o120000
    return (
        bool(member.filename)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in member.filename
        and not is_symlink
        and member.file_size <= MAX_ARCHIVE_MEMBER_BYTES
    )


def _remove_old_backups(parent):
    for path in parent.glob(f"{BACKUP_PREFIX}*"):
        if path.is_dir() and not path.is_symlink():
            _remove_tree(path)


def _remove_tree(path):
    try:
        if Path(path).is_dir() and not Path(path).is_symlink():
            shutil.rmtree(path)
    except OSError:
        pass
