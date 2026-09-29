"""Load release tables from a local copy or from the Hugging Face Hub.

    from boards_in_the_wild.load import load_table
    mappings = load_table("mappings")                 # pyarrow.Table
    photos = load_table("photos").to_pandas()         # pandas.DataFrame

``data_dir`` may point at a local snapshot (the directory holding ``manifest.json``).
Otherwise the file is fetched with ``huggingface_hub`` (cached) at ``revision``
(a tag such as ``v1.0.0`` or a commit hash; default ``main``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .schemas import HF_REPO_ID, TABLES


def resolve_file(relative_path: str, data_dir: str | Path | None = None,
                 revision: str | None = None, repo_id: str = HF_REPO_ID) -> Path:
    """Return a local path for a release file, downloading it from the Hub if needed."""

    if data_dir is not None:
        path = Path(data_dir) / relative_path
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    from huggingface_hub import hf_hub_download

    return Path(hf_hub_download(repo_id=repo_id, repo_type="dataset", filename=relative_path,
                                revision=revision))


def load_table(name: str, data_dir: str | Path | None = None, revision: str | None = None,
               columns: list[str] | None = None, repo_id: str = HF_REPO_ID) -> pa.Table:
    if name not in TABLES:
        raise KeyError(f"unknown table {name!r}; known: {sorted(TABLES)}")
    path = resolve_file(TABLES[name][0], data_dir, revision, repo_id)
    return pq.read_table(path, columns=columns)


def load_manifest(data_dir: str | Path | None = None, revision: str | None = None,
                  repo_id: str = HF_REPO_ID) -> dict:
    return json.loads(resolve_file("manifest.json", data_dir, revision, repo_id).read_text())


def schema_version(name: str, data_dir: str | Path | None = None,
                   revision: str | None = None) -> str:
    path = resolve_file(TABLES[name][0], data_dir, revision)
    meta = pq.read_schema(path).metadata or {}
    return meta.get(b"boards_in_the_wild.schema_version", b"").decode()
