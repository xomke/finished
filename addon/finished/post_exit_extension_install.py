"""Standalone helper run by Blender's bundled Python after Blender exits."""

import argparse
import json
from pathlib import Path
import os
import subprocess
import sys
import time


DEFAULT_TIMEOUT_SECONDS = 10 * 60
POLL_SECONDS = 0.5


def blender_install_command(blender_binary, package_path, repository):
    return [str(Path(blender_binary)), "--command", "extension", "install-file", "--repo", repository,
            "--enable", str(Path(package_path))]


def wait_for_exit(process_id, *, timeout_seconds=DEFAULT_TIMEOUT_SECONDS, sleep=time.sleep, clock=time.monotonic):
    if process_id <= 0:
        raise ValueError("process_id must be positive")
    deadline = clock() + timeout_seconds
    while _process_exists(process_id):
        if clock() >= deadline:
            raise TimeoutError("timed out waiting for Blender to close")
        sleep(POLL_SECONDS)


def _process_exists(process_id):
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if sys.platform == "darwin" and _macos_process_is_zombie(process_id):
        return False
    return True


def _macos_process_is_zombie(process_id):
    try:
        completed = subprocess.run(["ps", "-o", "stat=", "-p", str(process_id)], check=False,
                                   capture_output=True, text=True)
    except OSError:
        return False
    return completed.returncode == 0 and completed.stdout.strip().startswith("Z")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install a Blender extension after Blender exits.")
    parser.add_argument("--wait-pid", type=int, required=True)
    parser.add_argument("--blender", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    blender_binary, package_path = Path(args.blender), Path(args.package)
    result_path = Path(args.result_file)
    if not blender_binary.is_absolute() or not blender_binary.is_file():
        parser.error("--blender must be an existing absolute file")
    if not package_path.is_absolute() or not package_path.is_file() or package_path.suffix.lower() != ".zip":
        parser.error("--package must be an existing absolute ZIP file")
    if not result_path.is_absolute() or result_path.parent != package_path.parent:
        parser.error("--result-file must be an absolute path beside the package")
    if not args.repo or any(character.isspace() for character in args.repo) or args.timeout_seconds <= 0:
        parser.error("invalid repository or timeout")
    exit_code = 1
    try:
        wait_for_exit(args.wait_pid, timeout_seconds=args.timeout_seconds)
        exit_code = subprocess.run(blender_install_command(blender_binary, package_path, args.repo), check=False).returncode
    except (OSError, TimeoutError):
        pass
    _write_result(result_path, exit_code)
    return exit_code


def _write_result(path, exit_code):
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        temporary.write_text(json.dumps({"schema_version": 1, "status": "installed" if exit_code == 0 else "failed"}), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
