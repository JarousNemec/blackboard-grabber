from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field

from .client import BlackboardClient
from .models import ContentItem
from .utils import BBCSWEBDAV_RE, clean_url, iso_now

logger = logging.getLogger("bb_backup.tree")


# Content handler patterny, které default-deselectneme s vysvětlujícím důvodem.
# Uživatel je v TUI vidí a může je vědomě zaškrtnout.
SKIP_PATTERNS: dict[str, str] = {
    "x-bb-asmt-test-link": "kvíz/test",
    "x-bb-asmt-survey-link": "anketa",
    "x-turnitin": "Turnitin (kvíz)",
    "x-osv-kaltura": "video Kaltura",
    "x-bb-panopto": "video Panopto",
    "x-bb-discussion": "diskuze",
    "x-bb-announcement": "oznámení",
    "x-bb-blogs": "blog",
    "x-bb-journal": "journal",
}

EXTERNAL_VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "panopto", "kaltura")


def _skip_reason(item: ContentItem) -> str | None:
    handler = item.contentHandler
    if handler is None:
        return None
    handler_id = (handler.id or "").lower()
    for pattern, reason in SKIP_PATTERNS.items():
        if pattern in handler_id:
            return reason
    if handler.url:
        url_low = handler.url.lower()
        for host in EXTERNAL_VIDEO_HOSTS:
            if host in url_low:
                return f"externí video ({host})"
    return None


class AttachmentNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    filename: str
    size: int | None = None
    # Pokud je nastavený, stahuj přes download_url místo /attachments/{id}/download.
    # Používá se pro Blackboard Ultra `resource/x-bb-file` items, které mají
    # metadata souboru v contentDetail a samotný soubor přes /bbcswebdav/...
    download_url: str | None = None


class TreeNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    content_handler: str | None = None
    selected: bool = True
    has_body: bool = False
    skipped_reason: str | None = None
    attachments: list[AttachmentNode] = Field(default_factory=list)
    children: list["TreeNode"] = Field(default_factory=list)


TreeNode.model_rebuild()


class CourseTree(BaseModel):
    model_config = ConfigDict(extra="ignore")

    course_id: str
    course_name: str = ""
    fetched_at: str
    root: list[TreeNode] = Field(default_factory=list)


def _file_to_node(f: dict, fallback_id: str) -> AttachmentNode | None:
    """Z metadata file dictu (z contentDetail) vyrobí AttachmentNode."""
    if not isinstance(f, dict):
        return None
    url = f.get("permanentUrl") or f.get("downloadUrl") or f.get("url")
    name = f.get("fileName") or f.get("linkName") or f.get("name")
    if not url or not name:
        return None
    return AttachmentNode(
        id=f.get("xid") or fallback_id,
        filename=name,
        size=f.get("fileSize") or f.get("size"),
        download_url=url,
    )


def _extract_body_files(item: ContentItem) -> list[AttachmentNode]:
    """Scanne HTML body a description pro pojmenované `bbcswebdav` URLs.

    Některé Ultra dokumenty linkují PDF/přílohy uvnitř `displayText` (typicky
    `<a href="@X@.../bbcswebdav/.../prednaska.pdf">stáhnout</a>`). xid-only
    URLs (bez filenamu v cestě) přeskakujeme — ty zpracuje až html_processor
    při stahování body, kde má k dispozici Content-Disposition pro reálné jméno.
    """
    sources: list[str] = []
    if item.body:
        sources.append(item.body)
    if item.description:
        sources.append(item.description)
    if not sources:
        return []

    out: list[AttachmentNode] = []
    seen: set[str] = set()
    for src in sources:
        for m in BBCSWEBDAV_RE.finditer(src):
            url = clean_url(m.group(0)).replace("&amp;", "")
            if url in seen:
                continue
            seen.add(url)
            last = unquote(url.rsplit("/", 1)[-1])
            if not last or last.startswith("xid-"):
                continue
            out.append(
                AttachmentNode(id=url, filename=last, download_url=url)
            )
    return out


def _extract_ultra_files(item: ContentItem) -> list[AttachmentNode]:
    """Blackboard Ultra ukládá soubory na několika místech:
    1) `fileAttachments.attachments` — z ?expand=fileAttachments (KLÍČOVÉ pro
       běžné x-bb-document položky s linkovaným PDF).
    2) `contentDetail.{handler}.file` (1 soubor) — pro x-bb-file typy.
    3) `contentDetail.{handler}.files` / `attachments` — plurální varianty.
    Posbírá vše, deduplikuje podle (xid|filename).
    """
    out: list[AttachmentNode] = []
    seen: set[str] = set()

    def _add(n: AttachmentNode | None) -> None:
        if n is None:
            return
        key = f"{n.id}|{n.filename}"
        if key in seen:
            return
        seen.add(key)
        out.append(n)

    # 1) fileAttachments — Ultra primary mechanism po expand=fileAttachments
    if item.fileAttachments:
        atts = item.fileAttachments.get("attachments")
        if isinstance(atts, list):
            for f in atts:
                _add(_file_to_node(f, item.id))

    # 2) + 3) contentDetail varianty
    if item.contentDetail:
        for handler_block in item.contentDetail.values():
            if not isinstance(handler_block, dict):
                continue
            _add(_file_to_node(handler_block.get("file") or {}, item.id))
            for plural_key in ("files", "attachments"):
                arr = handler_block.get(plural_key)
                if isinstance(arr, list):
                    for f in arr:
                        _add(_file_to_node(f, item.id))
    return out


def _build_node(
    client: BlackboardClient,
    course_id: str,
    item: ContentItem,
    *,
    default_select_all: bool,
) -> TreeNode:
    skipped = _skip_reason(item)
    selected = default_select_all and skipped is None

    attachments: list[AttachmentNode] = []
    seen_keys: set[str] = set()

    def _add_unique(node: AttachmentNode) -> None:
        # Dedup podle stejného download URL (pokud má) nebo jména souboru.
        key = (node.download_url or "") + "|" + node.filename
        if key in seen_keys:
            return
        seen_keys.add(key)
        attachments.append(node)

    if not skipped:
        # 1) Soubory z contentDetail (Ultra struktura)
        for a in _extract_ultra_files(item):
            _add_unique(a)

        # 2) bbcswebdav URLs uvnitř HTML body / description
        for a in _extract_body_files(item):
            _add_unique(a)

        # 3) Legacy /attachments endpoint (Ultra většinou 404, legacy instance ano).
        # Defensive catch: jeden problémový item nesmí shodit walk celého kurzu.
        try:
            for att in client.get_attachments(course_id, item.id):
                _add_unique(
                    AttachmentNode(id=att.id, filename=att.fileName, size=att.size)
                )
        except Exception as e:
            logger.warning("nelze získat přílohy pro %s: %s", item.id, e)

    children: list[TreeNode] = []
    if item.is_folder_like and not skipped:
        # Defensive catch: viz výše.
        try:
            kids = client.get_children(course_id, item.id)
        except Exception as e:
            logger.warning("nelze získat děti pro %s: %s", item.id, e)
            kids = []
        kids.sort(key=lambda c: (c.position, c.title))
        for child in kids:
            children.append(
                _build_node(client, course_id, child, default_select_all=default_select_all)
            )

    return TreeNode(
        id=item.id,
        title=item.title or "(bez názvu)",
        content_handler=(item.contentHandler.id if item.contentHandler else None),
        selected=selected,
        has_body=bool(item.body),
        skipped_reason=skipped,
        attachments=attachments,
        children=children,
    )


def walk_course(
    client: BlackboardClient,
    course_id: str,
    *,
    default_select_all: bool,
    course_name: str = "",
) -> CourseTree:
    """Sestaví kompletní strom obsahu kurzu."""
    logger.info("Walking course %s", course_id)
    roots = client.get_course_contents(course_id)
    roots.sort(key=lambda c: (c.position, c.title))
    nodes = [
        _build_node(client, course_id, r, default_select_all=default_select_all)
        for r in roots
    ]
    return CourseTree(
        course_id=course_id,
        course_name=course_name,
        fetched_at=iso_now(),
        root=nodes,
    )


def save_tree(tree: CourseTree, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = tree.model_dump()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_tree(path: Path) -> CourseTree:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return CourseTree.model_validate(raw)


# ---------- statistiky a iterace přes vybrané položky ----------


def iter_selected(node: TreeNode, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], TreeNode]]:
    """Vrátí seznam (cesta_titlů, node) pro vybrané položky včetně rekurze."""
    out: list[tuple[tuple[str, ...], TreeNode]] = []
    path = prefix + (node.title,)
    if node.selected:
        out.append((path, node))
    for child in node.children:
        out.extend(iter_selected(child, path))
    return out


def count_nodes(roots: list[TreeNode]) -> tuple[int, int, int]:
    """(total_nodes, selected_nodes, total_attachment_bytes_for_selected)."""
    total = 0
    selected = 0
    bytes_ = 0
    stack = list(roots)
    while stack:
        n = stack.pop()
        total += 1
        if n.selected:
            selected += 1
            for att in n.attachments:
                if att.size:
                    bytes_ += att.size
        stack.extend(n.children)
    return total, selected, bytes_
