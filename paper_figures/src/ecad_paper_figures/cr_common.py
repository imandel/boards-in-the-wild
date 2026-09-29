"""Shared helpers for the camera-ready paper analyses (paper-cr-* namespaces).

Every analysis reads receipt-bound inputs whose SHA-256 is given on the command line or in
a config, writes into a temporary sibling directory, and promotes it atomically with an
explicit ``success.json``. A completed output with the same request fingerprint is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def verify_inputs(inputs: dict[str, dict[str, str]]) -> dict[str, str]:
    """Check every ``{name: {path, sha256}}`` input; return name -> sha256."""
    verified = {}
    for name, spec in sorted(inputs.items()):
        path = Path(os.path.expandvars(spec["path"]))  # $ECAD_RUNS / $ECAD_DATA
        actual = sha256_file(path)
        if actual != spec["sha256"]:
            raise ValueError(f"{name}: sha256 {actual} != expected {spec['sha256']} ({path})")
        verified[name] = actual
    return verified


def code_revision(repo_hint: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_hint), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def tex_number(value: int) -> str:
    return f"{value:,}"


def tex_percent(numerator: int, denominator: int, digits: int = 1) -> str:
    return f"{100 * numerator / denominator:.{digits}f}\\%"


def write_macros(path: Path, macros: dict[str, str], header: str) -> None:
    """Write ``\\newcommand`` lines in the paper's numbers.tex style (trailing space)."""
    lines = [f"% {line}" if line else "%" for line in header.splitlines()]
    for name, value in macros.items():
        lines.append(f"\\newcommand{{\\{name}}}{{{value} }}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def promote(
    output: Path,
    request: dict[str, Any],
    build: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    """Run ``build(tmpdir)`` and atomically promote; identical completed reruns are no-ops."""
    output = Path(output)
    request_fp = fingerprint(request)
    success_path = output / "success.json"
    if success_path.exists():
        success = json.loads(success_path.read_text())
        if success.get("request_fingerprint") != request_fp:
            raise FileExistsError(f"{output} holds a different request; use a new namespace")
        for name, digest in success["outputs"].items():
            if sha256_file(output / name) != digest:
                raise ValueError(f"{output / name} no longer matches its success receipt")
        return success
    tmp = output.with_name(output.name + f".tmp-{os.getpid()}")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    write_json(tmp / "request.json", request)
    summary = build(tmp)
    write_json(tmp / "summary.json", summary)
    outputs = {
        p.name: sha256_file(p) for p in sorted(tmp.iterdir()) if p.is_file() and p.name != "success.json"
    }
    success = {
        "outputs": outputs,
        "request_fingerprint": request_fp,
        "schema_version": request["schema_version"] + "-success",
    }
    write_json(tmp / "success.json", success)
    if output.exists():
        raise FileExistsError(f"{output} exists without success.json; inspect before removing")
    tmp.rename(output)
    return success
