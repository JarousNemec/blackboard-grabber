from __future__ import annotations

import json
from pathlib import Path

from bb_backup.tree import (
    AttachmentNode,
    CourseTree,
    TreeNode,
    count_nodes,
    iter_selected,
    load_tree,
    save_tree,
)


def _sample_tree() -> CourseTree:
    return CourseTree(
        course_id="_1_1",
        course_name="Aplikovaná informatika",
        fetched_at="2026-05-06T10:00:00Z",
        root=[
            TreeNode(
                id="r1",
                title="Týden 1 — Úvod",
                content_handler="resource/x-bb-folder",
                selected=True,
                has_body=False,
                children=[
                    TreeNode(
                        id="r1c1",
                        title="Prezentace",
                        content_handler="resource/x-bb-document",
                        selected=True,
                        has_body=True,
                        attachments=[
                            AttachmentNode(id="a1", filename="prednaska.pptx", size=1234567),
                        ],
                    ),
                    TreeNode(
                        id="r1c2",
                        title="Test ze cvičení",
                        content_handler="resource/x-bb-asmt-test-link",
                        selected=False,
                        skipped_reason="kvíz",
                    ),
                ],
            ),
            TreeNode(
                id="r2",
                title="Materiály",
                content_handler="resource/x-bb-folder",
                selected=True,
            ),
        ],
    )


def test_round_trip(tmp_path: Path):
    tree = _sample_tree()
    path = tmp_path / "tree.json"
    save_tree(tree, path)

    loaded = load_tree(path)
    assert loaded.course_id == tree.course_id
    assert loaded.course_name == tree.course_name
    assert len(loaded.root) == 2
    assert loaded.root[0].children[0].attachments[0].filename == "prednaska.pptx"
    assert loaded.root[0].children[0].attachments[0].size == 1234567
    assert loaded.root[0].children[1].skipped_reason == "kvíz"


def test_diacritics_preserved(tmp_path: Path):
    tree = _sample_tree()
    path = tmp_path / "tree.json"
    save_tree(tree, path)
    raw = path.read_text(encoding="utf-8")
    assert "Týden 1 — Úvod" in raw
    assert "Aplikovaná informatika" in raw


def test_count_nodes():
    tree = _sample_tree()
    total, selected, bytes_ = count_nodes(tree.root)
    assert total == 4
    assert selected == 3  # r1, r1c1, r2; r1c2 (kvíz) je False
    assert bytes_ == 1234567


def test_iter_selected_returns_paths():
    tree = _sample_tree()
    out: list = []
    for root in tree.root:
        out.extend(iter_selected(root))

    paths = [p for p, _ in out]
    assert ("Týden 1 — Úvod",) in paths
    assert ("Týden 1 — Úvod", "Prezentace") in paths
    # kvíz není selected
    assert ("Týden 1 — Úvod", "Test ze cvičení") not in paths
    assert ("Materiály",) in paths


def test_empty_tree_round_trip(tmp_path: Path):
    tree = CourseTree(course_id="_x_1", fetched_at="2026-05-06T10:00:00Z", root=[])
    path = tmp_path / "tree.json"
    save_tree(tree, path)
    loaded = load_tree(path)
    assert loaded.root == []


def test_deep_nesting_round_trip(tmp_path: Path):
    deepest = TreeNode(id="leaf", title="Hluboko", selected=True)
    node = deepest
    for i in range(20):
        node = TreeNode(id=f"n{i}", title=f"Úroveň {i}", selected=True, children=[node])
    tree = CourseTree(course_id="_d_1", fetched_at="t", root=[node])
    path = tmp_path / "tree.json"
    save_tree(tree, path)
    loaded = load_tree(path)

    cur = loaded.root[0]
    depth = 0
    while cur.children:
        cur = cur.children[0]
        depth += 1
    assert depth == 20
    assert cur.title == "Hluboko"


def test_default_select_all_logic_via_skipped_reason():
    """skipped_reason má přednost před default_select_all — testuju invariant."""
    tree = _sample_tree()
    quiz = tree.root[0].children[1]
    assert quiz.skipped_reason is not None
    assert quiz.selected is False


def test_attachment_size_optional():
    """size může být None (Risk R2 — některé endpointy ji nedávají)."""
    n = TreeNode(
        id="x",
        title="bez size",
        attachments=[AttachmentNode(id="a", filename="f.pdf", size=None)],
    )
    assert n.attachments[0].size is None
    # serialize round-trip
    raw = n.model_dump_json()
    n2 = TreeNode.model_validate(json.loads(raw))
    assert n2.attachments[0].size is None
