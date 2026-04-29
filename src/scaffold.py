"""Bootstrap a working config directory from bundled sample files."""

from __future__ import annotations

import shutil
from importlib.resources import as_file, files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def write_sample_config(target: Path) -> list[Path]:
    """Copy bundled sample config files into *target*.

    Creates ``defaults.yaml``, ``projects/minimal.yaml``, and the two
    ``federation_static*.json`` examples.

    Returns the list of files written.

    Raises:
        FileExistsError: if *target* already contains YAML files.
    """
    existing_yaml = list(target.glob("**/*.yaml"))
    if existing_yaml:
        msg = f"Config directory already contains YAML files: {target}"
        raise FileExistsError(msg)

    sample_pkg = files("src.sample_config")
    written: list[Path] = []

    # Top-level files
    for name in ("defaults.yaml", "federation_static.json", "federation_static_group.json"):
        src = sample_pkg.joinpath(name)
        dest = target / name
        with as_file(src) as src_path:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest)
            written.append(dest)

    # projects/ subdirectory — ship only the minimal example
    src = sample_pkg.joinpath("projects", "minimal.yaml")
    dest = target / "projects" / "minimal.yaml"
    with as_file(src) as src_path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest)
        written.append(dest)

    return written
