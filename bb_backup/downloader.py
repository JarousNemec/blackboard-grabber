from __future__ import annotations

import hashlib
import json
import logging
import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .client import AuthError, BlackboardClient, BlackboardError
from .config import Config
from .logging_setup import errors_log_path
from .tree import AttachmentNode, CourseTree, TreeNode, iter_selected
from .utils import clean_url, iso_now, long_path, sanitize_filename

logger = logging.getLogger("bb_backup.downloader")

_HASH_BLOCK_SIZE = 64 * 1024


@dataclass
class DownloadStats:
    skipped: int = 0
    new: int = 0
    errored: int = 0
    bytes_downloaded: int = 0


@dataclass
class _ManifestEntry:
    path: str
    sha256: str
    size: int
    content_id: str
    attachment_id: str | None
    downloaded_at: str

    def to_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "size": self.size,
            "content_id": self.content_id,
            "attachment_id": self.attachment_id,
            "downloaded_at": self.downloaded_at,
        }


class Downloader:
    """Stáhne všechny `selected: true` položky stromu kurzu."""

    def __init__(
        self,
        client: BlackboardClient,
        config: Config,
        course_id: str,
        tree: CourseTree,
        *,
        html_processor: Callable[[str, BlackboardClient, str, str, Path], str] | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.course_id = course_id
        self.tree = tree
        self.html_processor = html_processor

        self.course_root = config.output_dir / sanitize_filename(
            tree.course_name or course_id
        )
        self.state_root = config.state_dir / course_id
        self.manifest_path = self.state_root / "manifest.json"
        self.errors_path = errors_log_path(config)

        self._manifest: dict[str, dict] = {}

    # -----------------------------
    # Manifest
    # -----------------------------

    def _load_manifest(self) -> None:
        if self.manifest_path.is_file():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                self._manifest = data.get("files", {})
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("manifest.json nelze načíst (%s), startuju s prázdným.", e)
                self._manifest = {}
        else:
            self._manifest = {}

    def _save_manifest(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "course_id": self.course_id,
            "course_name": self.tree.course_name,
            "updated_at": iso_now(),
            "files": self._manifest,
        }
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.manifest_path)

    # -----------------------------
    # Errors log
    # -----------------------------

    def _log_error(self, rel_path: Path, exc: Exception) -> None:
        self.errors_path.parent.mkdir(parents=True, exist_ok=True)
        with self.errors_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[{iso_now()}] {self.course_id} :: {rel_path}\n")
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

    # -----------------------------
    # Path resolution
    # -----------------------------

    def _node_dir(self, path_titles: tuple[str, ...]) -> Path:
        """Z (Týden 1, Prezentace) odvodí output_dir/<course>/Týden 1/Prezentace."""
        target = self.course_root
        for title in path_titles:
            target = target / sanitize_filename(title)
        return target

    # -----------------------------
    # File primitives
    # -----------------------------

    def _file_already_done(self, rel_path: str, abs_path: Path, expected_size: int | None) -> bool:
        lp = long_path(abs_path)
        if not os.path.isfile(lp):
            return False
        if not self.config.download.verify_size:
            return rel_path in self._manifest
        if expected_size is None:
            return rel_path in self._manifest
        try:
            actual = os.stat(lp).st_size
        except OSError:
            return False
        return actual == expected_size

    def _sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(long_path(path), "rb") as f:
            for chunk in iter(lambda: f.read(_HASH_BLOCK_SIZE), b""):
                h.update(chunk)
        return h.hexdigest()

    # -----------------------------
    # Per-item operations
    # -----------------------------

    def _download_attachment(
        self,
        node: TreeNode,
        att: AttachmentNode,
        node_dir: Path,
        progress: Progress,
        task_id: int,
    ) -> tuple[bool, int]:
        """Vrátí (skipped, bytes_downloaded)."""
        filename = sanitize_filename(att.filename)
        abs_path = node_dir / filename
        rel_path = str(abs_path.relative_to(self.course_root.parent))

        if self._file_already_done(rel_path, abs_path, att.size):
            progress.update(task_id, description=f"[skip] {filename}", advance=0)
            return True, 0

        os.makedirs(long_path(node_dir), exist_ok=True)
        progress.update(task_id, description=f"[new] {filename}")
        if att.download_url:
            # Ultra: stáhni přímo přes URL z contentDetail.{handler}.file.permanentUrl
            size = self.client.download_url(att.download_url, abs_path)
        else:
            # Legacy: klasický /attachments/{id}/download endpoint
            size = self.client.download_attachment(
                self.course_id, node.id, att.id, abs_path
            )
        self._manifest[rel_path] = _ManifestEntry(
            path=rel_path,
            sha256=self._sha256(abs_path),
            size=size,
            content_id=node.id,
            attachment_id=att.id,
            downloaded_at=iso_now(),
        ).to_dict()
        return False, size

    def _download_html_body(
        self,
        node: TreeNode,
        node_dir: Path,
        skip_urls: dict[str, str] | None = None,
    ) -> tuple[bool, int]:
        """Stáhne body content jako index.html. Vrací (skipped, bytes_downloaded).

        `skip_urls` je {bbcswebdav_url: filename} mapa souborů, které byly
        stažené už dřív (jako attachmenty top-level). Pro tyto URL html_processor
        jen přepíše odkaz, znovu nestahuje.
        """
        index_path = node_dir / "index.html"
        rel_path = str(index_path.relative_to(self.course_root.parent))

        # Body je v ContentItem detail endpointu — refetchneme pro jistotu.
        try:
            item = self.client.get_content_item(self.course_id, node.id)
        except AuthError:
            raise
        except BlackboardError as e:
            logger.warning("nelze získat body pro %s: %s", node.id, e)
            return False, 0

        if not item.body:
            return False, 0

        os.makedirs(long_path(node_dir), exist_ok=True)
        rendered = item.body
        if self.html_processor is not None:
            try:
                rendered = self.html_processor(
                    item.body, self.client, self.course_id, node.id, node_dir,
                    skip_urls=skip_urls or {},
                )
            except AuthError:
                raise
            except Exception as e:  # HTML processing nesmí shodit celý job
                logger.warning("html_processor selhal pro %s: %s", node.id, e)
                rendered = item.body

        wrapped = (
            "<!DOCTYPE html>\n<html><head>"
            f"<meta charset='utf-8'><title>{node.title}</title></head>"
            f"<body>{rendered}</body></html>\n"
        )
        tmp = index_path.with_suffix(".html.tmp")
        with open(long_path(tmp), "w", encoding="utf-8") as f:
            f.write(wrapped)
        os.replace(long_path(tmp), long_path(index_path))
        size = os.stat(long_path(index_path)).st_size
        self._manifest[rel_path] = _ManifestEntry(
            path=rel_path,
            sha256=self._sha256(index_path),
            size=size,
            content_id=node.id,
            attachment_id=None,
            downloaded_at=iso_now(),
        ).to_dict()
        return False, size

    # -----------------------------
    # Run
    # -----------------------------

    def run(self) -> DownloadStats:
        self._load_manifest()
        os.makedirs(long_path(self.course_root), exist_ok=True)

        selected: list[tuple[tuple[str, ...], TreeNode]] = []
        for root in self.tree.root:
            selected.extend(iter_selected(root))

        stats = DownloadStats()
        total_planned = sum(
            (1 if node.has_body else 0) + len(node.attachments)
            for _, node in selected
            if not node.skipped_reason
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            TextColumn("{task.completed}/{task.total}"),
        ) as progress:
            task_id = progress.add_task("Stahuji...", total=total_planned)

            for path_titles, node in selected:
                if node.skipped_reason:
                    continue
                node_dir = self._node_dir(path_titles)

                # 1) Nejdřív přílohy (top-level soubory v node_dir).
                # Map URL -> sanitized filename, kterou pak předáme do html_processoru,
                # aby místo opakovaného downloadu jen přepsal odkaz.
                skip_urls: dict[str, str] = {}
                for att in node.attachments:
                    try:
                        skipped, n_bytes = self._download_attachment(
                            node, att, node_dir, progress, task_id
                        )
                    except AuthError:
                        progress.stop()
                        self._save_manifest()
                        raise
                    except Exception as e:
                        # Defensive: jeden zlobivý soubor nesmí shodit zbytek běhu.
                        logger.error(
                            "příloha %s/%s selhala: %s", node.id, att.filename, e
                        )
                        self._log_error(node_dir / att.filename, e)
                        stats.errored += 1
                    else:
                        if skipped:
                            stats.skipped += 1
                        else:
                            stats.new += 1
                            stats.bytes_downloaded += n_bytes
                        # Mapa URL → lokální jméno pro html_processor (zabrání
                        # duplicitnímu downloadu při rewrite linků v body HTML).
                        if att.download_url and "/bbcswebdav/" in att.download_url:
                            skip_urls[clean_url(att.download_url)] = sanitize_filename(
                                att.filename
                            )
                    progress.update(task_id, advance=1)

                # 2) Pak body — html_processor přepíše odkazy na ty soubory,
                # co se právě stáhly jako přílohy (žádný duplicitní download).
                if node.has_body:
                    try:
                        skipped, n_bytes = self._download_html_body(
                            node, node_dir, skip_urls=skip_urls
                        )
                    except AuthError:
                        progress.stop()
                        self._save_manifest()
                        raise
                    except Exception as e:
                        # Defensive: viz výše.
                        logger.error("body %s selhal: %s", node.id, e)
                        self._log_error(node_dir / "index.html", e)
                        stats.errored += 1
                    else:
                        if skipped:
                            stats.skipped += 1
                        else:
                            stats.new += 1
                            stats.bytes_downloaded += n_bytes
                    progress.update(task_id, advance=1)

                # Periodicky ukládáme manifest, abychom o nic nepřišli při Ctrl+C.
                self._save_manifest()

        self._save_manifest()
        return stats
