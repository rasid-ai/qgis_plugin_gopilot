#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build QGIS Plugin ZIP
Creates a QGIS-compatible plugin package.
"""

import sys
import zipfile
from pathlib import Path

# Fix Windows UTF-8 console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# IMPORTANT:
# Must be a valid Python package name
PLUGIN_NAME = "rasid_plugin"

# Files in plugin root
FILES_TO_COPY = [
    "__init__.py",
    "metadata.txt",
    "rasid_plugin.py",
    "resources.py",
    "icon.png",
    "icon_nobg.png",
    "auth-topo.jpg",
    "gopilot-mark.svg",
    "gopilot-mark-reversed.svg",
    "LICENSE",
    "README.md",
    "logo.png",
    "rasid_logo.svg",
    "requirements.txt",  # read at runtime by venv_manager to know what to install
]

# Directories to include
DIRS_TO_COPY = [
    "rasid_components",
    "i18n",
]

# Ignore patterns
EXCLUDE_PATTERNS = {
    "__pycache__",
    ".git",
    ".gitignore",
    ".DS_Store",
    "tests",
    "test",
}


def should_exclude(path: Path):
    """Check if path should be excluded."""

    # Exclude folders/files by name
    for part in path.parts:
        if part in EXCLUDE_PATTERNS:
            return True

    # Exclude compiled Python files
    if path.suffix in {".pyc", ".pyo"}:
        return True

    return False


def create_plugin_zip():
    """Create plugin ZIP."""

    script_dir = Path(__file__).parent.resolve()

    zip_path = script_dir / f"{PLUGIN_NAME}.zip"

    print("=" * 60)
    print(f"Building plugin: {PLUGIN_NAME}")
    print("=" * 60)

    # Remove old ZIP
    if zip_path.exists():
        zip_path.unlink()
        print(f"Removed old ZIP: {zip_path.name}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:

        # Add root files
        for file_name in FILES_TO_COPY:

            file_path = script_dir / file_name

            if not file_path.exists():
                print(f"[WARNING] Missing file: {file_name}")
                continue

            arcname = f"{PLUGIN_NAME}/{file_name}"

            zipf.write(file_path, arcname)

            print(f"Added: {arcname}")

        # Add directories recursively
        for dir_name in DIRS_TO_COPY:

            dir_path = script_dir / dir_name

            if not dir_path.exists():
                print(f"[WARNING] Missing directory: {dir_name}")
                continue

            for file_path in dir_path.rglob("*"):

                if not file_path.is_file():
                    continue

                if should_exclude(file_path):
                    continue

                relative_path = file_path.relative_to(script_dir)

                arcname = f"{PLUGIN_NAME}/{relative_path}"

                zipf.write(file_path, arcname)

                print(f"Added: {arcname}")

        # Debug ZIP contents
        print("\nZIP CONTENTS:")
        print("-" * 60)

        for name in zipf.namelist():
            print(name)

    print("\n" + "=" * 60)
    print("SUCCESS")
    print("=" * 60)
    print(f"Created: {zip_path}")
    print("\nUpload THIS file to QGIS repository.")
    print("=" * 60)


if __name__ == "__main__":

    try:
        create_plugin_zip()

    except Exception as e:

        print(f"\nERROR: {e}")

        import traceback
        traceback.print_exc()

        sys.exit(1)