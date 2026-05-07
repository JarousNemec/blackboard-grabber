from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Static, Tree
from textual.widgets.tree import TreeNode as WidgetTreeNode

from .tree import CourseTree, TreeNode, count_nodes, save_tree
from .utils import format_size


def _summary(node: TreeNode) -> tuple[int, int]:
    """Vrátí (selected_count, total_count) pro node + všechny potomky.
    Skipped items se do totalu nepočítají (defaultně se nestahují)."""
    if node.skipped_reason:
        return (0, 0)
    sel = 1 if node.selected else 0
    total = 1
    for c in node.children:
        s, t = _summary(c)
        sel += s
        total += t
    return sel, total


def _label_for(node: TreeNode) -> str:
    if node.skipped_reason:
        return (
            f"[red]✗[/red]  [strike dim]{node.title}[/strike dim]  "
            f"[yellow]({node.skipped_reason})[/yellow]"
        )

    sel, total = _summary(node)
    if total <= 1:
        # Leaf — vlastní stav
        if node.selected:
            box = "[bold green]☑[/bold green]"
            title_style = "bold"
        else:
            box = "[dim]☐[/dim]"
            title_style = "dim"
        counter = ""
    else:
        if sel == total:
            box = "[bold green]☑[/bold green]"
            title_style = "bold"
            counter = ""
        elif sel == 0:
            box = "[dim]☐[/dim]"
            title_style = "dim"
            counter = ""
        else:
            box = "[yellow]◧[/yellow]"
            title_style = "bold yellow"
            counter = f"  [dim]({sel}/{total})[/dim]"

    # Mini-výpis souborů, které se u téhle položky stáhnou (rychlý přehled).
    parts: list[str] = []
    if node.has_body:
        parts.append("html")
    if node.attachments:
        parts.append(f"{len(node.attachments)} soub.")
    art = f"  [cyan dim]· {' + '.join(parts)}[/cyan dim]" if parts else ""

    return f"{box}  [{title_style}]{node.title}[/{title_style}]{art}{counter}"


class ConfirmQuitScreen(ModalScreen[bool]):
    BINDINGS = [
        ("y", "confirm", "Ano"),
        ("n", "cancel", "Ne"),
        ("escape", "cancel", "Zrušit"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="confirm"):
            yield Static("Máš neuložené změny. Skončit bez uložení? (y/n)")
            yield Button("Skončit", id="quit", variant="error")
            yield Button("Zrušit", id="cancel", variant="primary")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "quit")


class PickerApp(App):
    """TUI pro per-položku výběr obsahu kurzu."""

    CSS = """
    Screen { layout: vertical; }
    #header-stats { padding: 0 1; height: 1; background: $boost; color: $text; }
    Tree { height: 1fr; }
    #confirm { width: 60; height: 9; border: thick $accent; background: $surface; padding: 1; align: center middle; }
    """

    BINDINGS = [
        Binding("enter", "toggle_node_open", "Otevřít/Zavřít", priority=True),
        Binding("space", "toggle_one", "Vybrat", priority=True),
        Binding("ctrl+a", "select_all", "Vše", priority=True),
        Binding("ctrl+s", "save", "Uložit", priority=True),
        Binding("ctrl+d", "download_now", "Stáhnout", priority=True),
        Binding("ctrl+c", "clear_all", "Vyčistit", priority=True),
        Binding("escape", "quit_app", "Konec", priority=True),
    ]

    def __init__(self, tree_data: CourseTree, save_path: Path) -> None:
        super().__init__()
        self.tree_data = tree_data
        self.save_path = save_path
        self.dirty = False
        self.exit_action: str | None = None  # "download" | None — čte wizard po .run()
        self._stats_widget: Static | None = None
        self._node_map: dict[int, TreeNode] = {}  # widget_node_id -> data node

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        self._stats_widget = Static("", id="header-stats")
        yield self._stats_widget
        title = self.tree_data.course_name or self.tree_data.course_id
        tree_widget: Tree[None] = Tree(title, id="content-tree")
        tree_widget.show_root = True
        tree_widget.root.expand()
        yield tree_widget
        yield Footer()

    def on_mount(self) -> None:
        widget = self.query_one("#content-tree", Tree)
        for data_node in self.tree_data.root:
            self._add_subtree(widget.root, data_node)
        widget.root.expand()
        self._refresh_stats()
        widget.focus()

    def _add_subtree(self, parent: WidgetTreeNode, data_node: TreeNode) -> None:
        # Položka je rozbalitelná, pokud má potomky NEBO konkrétní soubory ke stažení.
        # Prázdné folders (žádný obsah) zůstávají leafy.
        has_artifacts = data_node.has_body or bool(data_node.attachments)
        has_kids = bool(data_node.children)

        if has_kids or has_artifacts:
            wnode = parent.add(
                _label_for(data_node),
                expand=(has_kids and not has_artifacts),
            )
        else:
            wnode = parent.add_leaf(_label_for(data_node))
        self._node_map[wnode.id] = data_node

        for child in data_node.children:
            self._add_subtree(wnode, child)

        # Info-listy pod každou položkou s body/přílohami — read-only, nejsou v _node_map.
        if data_node.has_body:
            wnode.add_leaf("[cyan]→[/cyan] [dim]index.html[/dim]")
        for att in data_node.attachments:
            size_str = f"  [dim]({format_size(att.size)})[/dim]" if att.size else ""
            wnode.add_leaf(f"[cyan]→[/cyan] [bold]{att.filename}[/bold]{size_str}")

    def _refresh_stats(self) -> None:
        total, selected, bytes_ = count_nodes(self.tree_data.root)
        dirty_marker = " * NEULOŽENO" if self.dirty else ""
        text = (
            f"Vybráno: {selected}/{total} položek"
            f"  •  Velikost příloh: {format_size(bytes_)}{dirty_marker}"
        )
        if self._stats_widget is not None:
            self._stats_widget.update(text)

    def _refresh_label(self, wnode: WidgetTreeNode) -> None:
        data = self._node_map.get(wnode.id)
        if data is not None:
            wnode.set_label(_label_for(data))

    def _refresh_ancestors(self, wnode: WidgetTreeNode) -> None:
        """Po toggle aktualizuj labels všech předků kvůli partial state ◧."""
        p = wnode.parent
        while p is not None:
            if p.id in self._node_map:
                self._refresh_label(p)
            p = p.parent

    def _selected_widget(self) -> WidgetTreeNode | None:
        widget = self.query_one("#content-tree", Tree)
        return widget.cursor_node

    def _walk_widget_subtree(self, wnode: WidgetTreeNode):
        yield wnode
        for child in wnode.children:
            yield from self._walk_widget_subtree(child)

    # ---------- akce ----------

    def action_toggle_one(self) -> None:
        wnode = self._selected_widget()
        if wnode is None or wnode.id not in self._node_map:
            return
        data = self._node_map[wnode.id]
        data.selected = not data.selected
        self.dirty = True
        self._refresh_label(wnode)
        self._refresh_ancestors(wnode)
        self._refresh_stats()

    def _set_all(self, value: bool) -> None:
        widget = self.query_one("#content-tree", Tree)
        for sub in self._walk_widget_subtree(widget.root):
            data = self._node_map.get(sub.id)
            if data is None or data.skipped_reason:
                continue
            data.selected = value
            self._refresh_label(sub)
        self.dirty = True
        self._refresh_stats()

    def action_select_all(self) -> None:
        self._set_all(True)

    def action_clear_all(self) -> None:
        self._set_all(False)

    def action_toggle_node_open(self) -> None:
        wnode = self._selected_widget()
        if wnode is None or not wnode.allow_expand:
            return
        if wnode.is_expanded:
            wnode.collapse()
        else:
            wnode.expand()

    def action_save(self) -> None:
        save_tree(self.tree_data, self.save_path)
        self.dirty = False
        self._refresh_stats()
        self.notify(f"Uloženo do {self.save_path.name}")

    def action_download_now(self) -> None:
        save_tree(self.tree_data, self.save_path)
        self.dirty = False
        self.exit_action = "download"
        self.exit()

    def action_quit_app(self) -> None:
        if not self.dirty:
            self.exit()
            return

        def after(confirmed: bool | None) -> None:
            if confirmed:
                self.exit()

        self.push_screen(ConfirmQuitScreen(), after)
