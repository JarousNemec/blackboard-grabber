from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .client import AuthError, BlackboardClient, BlackboardError
from .config import ConfigError, load_config
from .logging_setup import setup_logging

if sys.platform == "win32":
    # Windows má default cp1252 stdout — zaručíme UTF-8, jinak Rich při výpisu
    # diakritiky padne. AttributeError pokud stream není TextIOWrapper, ValueError
    # pro neplatné kódování — obojí je no-op fallback (uživatel uvidí cp1252 výstup).
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

app = typer.Typer(
    name="bb-backup",
    help="Lokální záloha kurzů z Blackboard Learn LMS.",
    no_args_is_help=False,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True, style="red")


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Bez argumentů spustí interaktivní wizard."""
    if ctx.invoked_subcommand is None:
        from .wizard import run_wizard

        run_wizard()


@app.command(help="Interaktivní wizard (alias k `bb-backup` bez argumentů).")
def wizard() -> None:
    from .wizard import run_wizard

    run_wizard()


def _read_resource(name: str) -> str:
    return resources.files("bb_backup._resources").joinpath(name).read_text(encoding="utf-8")


@app.command(help="Vytvoří config.example.toml a .gitignore v aktuálním adresáři.")
def init() -> None:
    cwd = Path.cwd()
    example_dst = cwd / "config.example.toml"
    gitignore_dst = cwd / ".gitignore"

    if example_dst.exists():
        console.print("[yellow]config.example.toml už existuje, přeskakuju.[/yellow]")
    else:
        example_dst.write_text(_read_resource("config.example.toml"), encoding="utf-8")
        console.print(f"[green]Vytvořeno:[/green] {example_dst.name}")

    if gitignore_dst.exists():
        console.print("[yellow].gitignore už existuje, přeskakuju.[/yellow]")
    else:
        gitignore_dst.write_text(_read_resource("gitignore.tpl"), encoding="utf-8")
        console.print(f"[green]Vytvořeno:[/green] {gitignore_dst.name}")

    console.print()
    console.print("Další kroky:")
    console.print("  1. Zkopíruj [bold]config.example.toml[/bold] jako [bold]config.toml[/bold]")
    console.print("  2. Vyplň [bold]base_url[/bold] své Blackboard instance")
    console.print("  3. Vyexportuj cookies z prohlížeče do [bold]cookies.txt[/bold]")
    console.print("  4. Spusť [bold]bb-backup probe[/bold]")


def _load_or_die():
    try:
        return load_config()
    except ConfigError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)


@app.command(help="Vypíše verzi.")
def version() -> None:
    console.print(f"bb-backup {__version__}")


@app.command(help="Ověří přihlášení a vypíše seznam kurzů.")
def probe(
    debug: bool = typer.Option(False, "--debug", help="Vypíše diagnostiku auth flow."),
) -> None:
    cfg = _load_or_die()
    setup_logging(cfg)

    try:
        client = BlackboardClient(cfg)
    except BlackboardError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)

    if debug:
        names = client.cookies_for_base()
        console.print(
            f"[dim]Cookies pro {client.base_url}:[/dim] "
            f"{', '.join(names) or '(žádné)'}"
        )

    try:
        me = client.get_me()
        console.print(f"[green]Přihlášen jako:[/green] {me.userName or me.id}")
        memberships = client.get_memberships_with_role()
    except AuthError as e:
        err_console.print(str(e))
        err_console.print()
        err_console.print(client.cookie_diagnostics())
        if debug:
            err_console.print()
            err_console.print(f"[dim]Cookies poslané na host:[/dim] {', '.join(client.cookies_for_base())}")
        raise typer.Exit(code=2)
    except BlackboardError as e:
        err_console.print(f"Chyba při komunikaci s Blackboardem: {e}")
        raise typer.Exit(code=1)

    if not memberships:
        console.print("[yellow]Žádné kurzy nenalezeny.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"Kurzy ({len(memberships)})")
    table.add_column("courseId", style="cyan", no_wrap=True)
    table.add_column("name")
    table.add_column("id", style="dim")
    table.add_column("role", style="magenta")

    for membership, course in memberships:
        if course is None:
            continue
        table.add_row(
            course.courseId,
            course.name,
            course.id,
            membership.courseRoleId or "-",
        )

    console.print(table)


@app.command(help="Stáhne strom obsahu kurzu do state/<courseId>/tree.json.")
def tree(
    course_id: str = typer.Argument(..., help="Course ID (např. _12345_1) z `bb-backup probe`."),
    force: bool = typer.Option(False, "--force", help="Přepiš existující tree.json (zahodí výběr!)."),
) -> None:
    from .tree import save_tree, walk_course

    cfg = _load_or_die()
    setup_logging(cfg)

    state_path = cfg.state_dir / course_id / "tree.json"
    if state_path.exists() and not force:
        err_console.print(
            f"{state_path} už existuje. Použij --force pro přepsání "
            f"(POZOR: zahodí ručně odškrtaný výběr)."
        )
        raise typer.Exit(code=2)

    try:
        client = BlackboardClient(cfg)
    except BlackboardError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)

    course_name = ""
    try:
        for course in client.get_my_courses():
            if course.id == course_id or course.courseId == course_id:
                course_name = course.name
                course_id = course.id
                break
    except AuthError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)
    except BlackboardError as e:
        err_console.print(f"Chyba: {e}")
        raise typer.Exit(code=1)

    console.print(f"Stahuji strom kurzu [bold]{course_name or course_id}[/bold]...")
    try:
        result = walk_course(
            client,
            course_id,
            default_select_all=cfg.tui.default_select_all,
            course_name=course_name,
        )
    except AuthError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)
    except BlackboardError as e:
        err_console.print(f"Chyba: {e}")
        raise typer.Exit(code=1)

    save_tree(result, state_path)

    from .tree import count_nodes

    total, selected, _ = count_nodes(result.root)
    console.print(
        f"[green]Hotovo:[/green] {state_path} ({total} položek, {selected} vybráno)"
    )


@app.command(help="Otevře TUI pro výběr položek ke stažení.")
def pick(
    course_id: str = typer.Argument(..., help="Course ID."),
) -> None:
    from .tree import load_tree
    from .tui import PickerApp

    cfg = _load_or_die()
    setup_logging(cfg)

    state_path = cfg.state_dir / course_id / "tree.json"
    if not state_path.is_file():
        err_console.print(
            f"{state_path} neexistuje. Spusť nejdřív `bb-backup tree {course_id}`."
        )
        raise typer.Exit(code=2)

    tree_data = load_tree(state_path)
    PickerApp(tree_data, state_path).run()


@app.command(help="Stáhne všechny vybrané položky kurzu do output adresáře.")
def download(
    course_id: str = typer.Argument(..., help="Course ID."),
) -> None:
    from .downloader import Downloader
    from .tree import load_tree

    cfg = _load_or_die()
    setup_logging(cfg)

    state_path = cfg.state_dir / course_id / "tree.json"
    if not state_path.is_file():
        err_console.print(
            f"{state_path} neexistuje. Spusť nejdřív `bb-backup tree {course_id}`."
        )
        raise typer.Exit(code=2)

    tree_data = load_tree(state_path)

    try:
        client = BlackboardClient(cfg)
    except BlackboardError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)

    from .html_processor import process_html

    dl = Downloader(client, cfg, course_id, tree_data, html_processor=process_html)
    try:
        stats = dl.run()
    except AuthError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)
    except BlackboardError as e:
        err_console.print(f"Chyba: {e}")
        raise typer.Exit(code=1)

    console.print()
    console.print(
        f"[green]Hotovo:[/green] [skip] {stats.skipped}  •  "
        f"[new] {stats.new}  •  [error] {stats.errored}"
    )
    if stats.bytes_downloaded:
        from .utils import format_size

        console.print(f"Staženo: {format_size(stats.bytes_downloaded)}")
    if stats.errored:
        console.print(
            f"[yellow]Chyby v {cfg.log_dir / 'errors.log'}[/yellow]"
        )


if __name__ == "__main__":
    app()
