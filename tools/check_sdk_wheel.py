#!/usr/bin/env python3
"""Verify that Host contract probes are running against an installed SDK wheel.

The root repository intentionally ignores the sibling ``mcbe-ws-sdk`` checkout.
This check therefore validates both the wheel archive and the import resolved by
the active interpreter.  It does not install anything and has no PyPI dependency;
the caller is responsible for building/installing the wheel in an isolated venv.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, NoReturn

EXPECTED_VERSION = "0.2.1"
DIST_NAME = "mcbe-ws-sdk"
PACKAGE_NAME = "mcbe_ws_sdk"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path, help="built mcbe-ws-sdk wheel")
    parser.add_argument(
        "--source-root",
        type=Path,
        help="optional root containing a nested SDK checkout; reject imports from it",
    )
    return parser.parse_args()


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"SDK wheel contract failed: {message}")


def _metadata_version(metadata_text: str) -> str | None:
    match = re.search(r"^Version:\s*([^\s]+)\s*$", metadata_text, flags=re.MULTILINE)
    return match.group(1) if match else None


def _assert_wheel_archive(wheel: Path) -> None:
    if not wheel.is_file() or wheel.suffix != ".whl":
        _fail(f"wheel does not exist or is not a .whl file: {wheel}")
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            metadata_names = [
                name for name in names if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                _fail(f"expected one dist-info/METADATA, found {metadata_names}")
            metadata = archive.read(metadata_names[0]).decode("utf-8")
            if _metadata_version(metadata) != EXPECTED_VERSION:
                _fail(f"wheel metadata is not version {EXPECTED_VERSION}")
            required_assets = {
                "mcbe_ws_sdk/profiles/mcbews_v1/manifest.json",
                "mcbe_ws_sdk/profiles/mcbews_v1/vectors.json",
            }
            missing = sorted(required_assets - names)
            if missing:
                _fail(f"wheel is missing protocol assets: {missing}")
    except zipfile.BadZipFile as exc:
        _fail(f"invalid wheel archive: {exc}")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_import_is_installed(source_root: Path | None) -> Any:
    try:
        distribution = importlib.metadata.distribution(DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        _fail(f"{DIST_NAME} is not installed in the active interpreter")
    if distribution.version != EXPECTED_VERSION:
        _fail(f"installed distribution is {distribution.version}, expected {EXPECTED_VERSION}")

    package_spec = importlib.util.find_spec(PACKAGE_NAME)
    if package_spec is None or package_spec.origin is None:
        _fail(f"cannot resolve installed package {PACKAGE_NAME}")
    package_path = Path(package_spec.origin).resolve()
    if source_root is not None:
        nested_sdk = (source_root / "mcbe-ws-sdk").resolve()
        if _is_under(package_path, nested_sdk):
            _fail(f"import resolved to nested SDK source checkout: {package_path}")
        source_path = (nested_sdk / "src").resolve()
        if any(_is_under(Path(entry or ".").resolve(), source_path) for entry in sys.path):
            _fail("nested SDK source directory is present on sys.path")

    import mcbe_ws_sdk

    if Path(mcbe_ws_sdk.__file__).resolve() != package_path:
        _fail("package spec and imported package path disagree")
    return mcbe_ws_sdk


def _assert_protocol_contract(sdk: Any) -> None:
    profile = sdk.MCBEWS_V1
    manifest = sdk.MCBEWS_V1_MANIFEST
    vectors = sdk.MCBEWS_V1_WIRE_VECTORS
    if profile.protocol_line != "MCBEWS/1":
        _fail("protocol compatibility line drifted")
    if profile.capability_request_schema_version != 2:
        _fail("capability request schema axis drifted")
    if profile.session_schema_version != 1:
        _fail("session schema axis drifted")
    if profile.text_response_framing_version != 1:
        _fail("text response framing axis drifted")
    if profile.ddui_persistence_version != 2:
        _fail("DDUI persistence axis drifted")
    if profile.command_line_byte_budget != 461:
        _fail("empirical 461-byte command budget drifted")
    if manifest["limits"]["command_line_budget_source"] != "empirical":
        _fail("command budget source is not marked empirical")
    if manifest["wire"]["trusted_bridge_player_name"] != "MCBEWS_BRIDGE":
        _fail("trusted ToolPlayer identity drifted")
    if manifest["text_response"]["usage_completion_only"] is not True:
        _fail("text response usage is not completion-frame-only")
    if not all(name in vectors for name in ("bridge_requests", "ui_chat", "text_response", "session", "approval")):
        _fail("wire vectors are incomplete")
    if not any(vector.get("name") == "session-switch-default" for vector in vectors["session"]):
        _fail("session-switch-default vector is missing")


def main() -> int:
    args = _parse_args()
    wheel = args.wheel.resolve()
    _assert_wheel_archive(wheel)
    sdk = _assert_import_is_installed(args.source_root.resolve() if args.source_root else None)
    _assert_protocol_contract(sdk)
    print(f"SDK wheel contract passed: {wheel.name}; import={sdk.__file__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
