"""Rehydrate photographs and CAD files from their recorded sources, verified by SHA-256.

The release hosts no photographs and only openly licensed CAD. Everything else is
retrieved from the recorded source and accepted **only** when its SHA-256 and byte size
equal the recorded values.

    python -m boards_in_the_wild.rehydrate photos --out rehydrated --mapping-ids m1 m2
    python -m boards_in_the_wild.rehydrate photos --out rehydrated --accepted --limit 100
    python -m boards_in_the_wild.rehydrate cad --out rehydrated --candidate-ids c1 c2
    python -m boards_in_the_wild.rehydrate cad --out rehydrated --mapping-ids m1

Retrieval recipes for CAD files (``cad_files.retrieval_recipe``):
  * mirrored files      -> ``mirror/raw/<sha[:2]>/<sha>`` in the dataset repository;
  * ``direct_file``      -> GET ``locator_value``;
  * ``repository_path``  -> raw file at ``repository``/``default_branch``/``repository_path``
                           (the branch may have moved; a hash mismatch is reported);
  * ``package_member``   -> GET ``source_package_url``, open the archive (nested zip/tar
                           archives are searched too) and take the member whose SHA-256
                           equals the recorded one (``archive_member_path_hint`` is a hint);
  * ``record_locator_only`` -> manual: open the record page and download by hand.

Every attempt is written to ``<out>/rehydrate_report.jsonl``; nothing is silently skipped.
Be polite: the default is one request at a time per host with a contact User-Agent.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .load import load_table, resolve_file

USER_AGENT = "boards-in-the-wild-rehydrate/1.0 (+https://huggingface.co/datasets/imandel/boards-in-the-wild)"
MAX_ARCHIVE_BYTES = 2 << 30


@dataclass
class Result:
    kind: str
    subject_id: str
    status: str
    url: str | None = None
    path: str | None = None
    detail: str | None = None

    def json(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def http_get(url: str, *, timeout: int = 60, retries: int = 2, pause: float = 0.5) -> bytes:
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(MAX_ARCHIVE_BYTES + 1)
            time.sleep(pause)
            if len(data) > MAX_ARCHIVE_BYTES:
                raise ValueError("response larger than the archive limit")
            return data
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def _write(out: Path, data: bytes) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    tmp.write_bytes(data)
    tmp.rename(out)
    return str(out)


def _verify(data: bytes, sha256: str, size: int | None) -> str | None:
    if size is not None and len(data) != size:
        return f"size {len(data)} != {size}"
    got = sha256_bytes(data)
    if got != sha256:
        return f"sha256 {got} != {sha256}"
    return None


# ----------------------------------------------------------------------------- photos
def rehydrate_photo(row: dict, out_dir: Path) -> Result:
    suffix = (row.get("suffix") or "").lower() or ".img"
    target = out_dir / "photos" / f"{row['photo_id']}{suffix}"
    if target.is_file() and sha256_bytes(target.read_bytes()) == row["sha256"]:
        return Result("photo", row["photo_id"], "present", row.get("direct_url"), str(target))
    url = row.get("direct_url")
    if not url and row.get("url_check") == "derived_from_document":
        return Result("photo", row["photo_id"], "derived_from_document", row.get("derived_from_url"), None,
                      f"image extracted from page {row.get('derived_from_page')} of this document; "
                      "extract it and verify sha256")
    if not url:
        return Result("photo", row["photo_id"], "no_direct_url", row.get("page_url"), None,
                      "only a page/repository URL is recorded; retrieve by hand and verify sha256")
    try:
        data = http_get(url)
    except Exception as error:  # noqa: BLE001
        return Result("photo", row["photo_id"], "download_failed", url, None, repr(error)[:300])
    problem = _verify(data, row["sha256"], row.get("size_bytes"))
    if problem:
        return Result("photo", row["photo_id"], "hash_mismatch", url, None, problem)
    return Result("photo", row["photo_id"], "ok", url, _write(target, data))


# ----------------------------------------------------------------------------- archives
def _members(data: bytes, depth: int = 0):
    """Yield (member path, bytes) for every file in a zip/tar archive, recursing once per level."""

    if depth > 3:
        return
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                payload = archive.read(info)
                yield info.filename, payload
                if info.filename.lower().endswith((".zip", ".tar", ".tgz", ".tar.gz")):
                    for sub, subdata in _members(payload, depth + 1):
                        yield f"{info.filename}/{sub}", subdata
        return
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                payload = handle.read()
                yield member.name, payload
    except tarfile.TarError:
        return


class ArchiveCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.index: dict[str, dict[str, bytes] | Exception] = {}

    def member_by_hash(self, url: str, sha256: str) -> tuple[str | None, bytes | None, str | None]:
        if url not in self.index:
            try:
                cached = self.cache_dir / sha256_bytes(url.encode())
                data = cached.read_bytes() if cached.is_file() else http_get(url, timeout=300)
                if not cached.is_file():
                    _write(cached, data)
                self.index[url] = {sha256_bytes(p): (name, p) for name, p in _members(data)}
            except Exception as error:  # noqa: BLE001
                self.index[url] = error
        entry = self.index[url]
        if isinstance(entry, Exception):
            return None, None, f"archive download failed: {entry!r}"[:300]
        hit = entry.get(sha256)
        if hit is None:
            return None, None, f"no member with sha256 {sha256} among {len(entry)} members"
        return hit[0], hit[1], None


# ----------------------------------------------------------------------------- CAD
def rehydrate_cad(row: dict, out_dir: Path, archives: ArchiveCache, data_dir, revision) -> Result:
    sha, size = row["content_sha256"], row.get("size_bytes")
    name = (row.get("relative_path") or sha).rsplit("/", 1)[-1]
    target = out_dir / "cad" / sha[:2] / sha / name
    if target.is_file() and sha256_bytes(target.read_bytes()) == sha:
        return Result("cad", row["file_id"], "present", None, str(target))
    recipe = row.get("retrieval_recipe")
    try:
        if row.get("distribution_class") == "mirrored" and row.get("mirror_path"):
            path = resolve_file(row["mirror_path"], data_dir, revision)
            data, url = path.read_bytes(), f"hf://{row['mirror_path']}"
        elif recipe == "direct_file":
            url = row["locator_value"]
            data = http_get(url)
        elif recipe == "repository_path":
            url = (f"https://raw.githubusercontent.com/{row['repository']}/"
                   f"{row['default_branch']}/{urllib.parse.quote(row['repository_path'])}")
            data = http_get(url)
        elif recipe == "package_member":
            url = row["source_package_url"]
            member, data, problem = archives.member_by_hash(url, sha)
            if problem:
                return Result("cad", row["file_id"], "archive_member_not_found", url, None, problem)
        else:
            return Result("cad", row["file_id"], "manual_retrieval_required",
                          row.get("locator_value"), None, "record locator only; see source_records.page_url")
    except Exception as error:  # noqa: BLE001
        return Result("cad", row["file_id"], "download_failed", locals().get("url"), None, repr(error)[:300])
    problem = _verify(data, sha, size)
    if problem:
        return Result("cad", row["file_id"], "hash_mismatch", url, None, problem)
    return Result("cad", row["file_id"], "ok", url, _write(target, data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=["photos", "cad"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=None, help="local snapshot (default: Hub)")
    parser.add_argument("--revision", default=None)
    parser.add_argument("--mapping-ids", nargs="*", default=None)
    parser.add_argument("--photo-ids", nargs="*", default=None)
    parser.add_argument("--candidate-ids", nargs="*", default=None,
                        help="board candidates (CAD board files); use --project to add their project files")
    parser.add_argument("--project", action="store_true", help="include all files of the candidates' project groups")
    parser.add_argument("--accepted", action="store_true", help="photos of accepted mappings only")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    report = (args.out / "rehydrate_report.jsonl").open("a")
    counts: dict[str, int] = {}
    mappings = None
    if args.mapping_ids:
        mappings = [m for m in load_table("mappings", args.data_dir, args.revision).to_pylist()
                    if m["mapping_id"] in set(args.mapping_ids)]
    if args.kind == "photos":
        rows = load_table("photos", args.data_dir, args.revision).to_pylist()
        wanted = set(args.photo_ids or [])
        if mappings is not None:
            wanted |= {m["photo_id"] for m in mappings}
        if wanted:
            rows = [r for r in rows if r["photo_id"] in wanted]
        elif args.accepted:
            rows = [r for r in rows if r["in_accepted_mapping"]]
        rows = rows[: args.limit] if args.limit else rows
        for row in rows:
            result = rehydrate_photo(row, args.out)
            report.write(result.json() + "\n")
            counts[result.status] = counts.get(result.status, 0) + 1
    else:
        files = load_table("cad_files", args.data_dir, args.revision).to_pylist()
        wanted = set(args.candidate_ids or [])
        if mappings is not None:
            for m in mappings:
                wanted |= set(m["cad_candidate_ids"] or [])
        if args.project:
            groups = {g for f in files if f["file_id"] in wanted for g in (f["project_group_ids"] or [])}
            rows = [f for f in files if f["file_id"] in wanted or set(f["project_group_ids"] or []) & groups]
        else:
            rows = [f for f in files if f["file_id"] in wanted]
        rows = rows[: args.limit] if args.limit else rows
        archives = ArchiveCache(args.out / ".archive_cache")
        for row in rows:
            result = rehydrate_cad(row, args.out, archives, args.data_dir, args.revision)
            report.write(result.json() + "\n")
            counts[result.status] = counts.get(result.status, 0) + 1
    report.close()
    print(json.dumps(counts, sort_keys=True))
    return 0 if set(counts) <= {"ok", "present"} else 1


if __name__ == "__main__":
    sys.exit(main())
