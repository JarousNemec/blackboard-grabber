"""Interaktivní wizard provazující init → probe → tree → pick → download.

Wizard je default chování `bb-backup` (bez argumentů). Stávajících pět
subkomand zůstává pro skripty a opakované volání.

Flow:
  1. Spočítat default cesty (cwd-based) a vytvořit adresáře.
  2. Pokud existuje config.toml, načíst hodnoty pro prefill.
  3. Prompt na base_url + cookies.txt (předvyplněno).
  4. Zapsat hodnoty zpět do config.toml.
  5. Validovat auth + vylistovat kurzy + výběr indexu.
  6. walk_course → save_tree → spustit PickerApp.
  7. Pokud uživatel stiskl Ctrl+D: prompt na výstupní adresář, potvrzení, spustit Downloader.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from .client import AuthError, BlackboardClient, BlackboardError
from .config import (
    Config,
    ConfigError,
    find_config_file,
    load_config,
    save_blackboard_settings,
)
from .downloader import Downloader
from .html_processor import process_html
from .logging_setup import setup_logging
from .models import Course, Membership
from .tree import save_tree, walk_course
from .tui import PickerApp
from .utils import format_size

console = Console()
err_console = Console(stderr=True, style="red")


# ---------- defaulty ----------


def _compute_defaults(cwd: Path) -> dict[str, str]:
    """Defaulty pro prompty. Pokud existuje config.toml v cwd nebo
    ~/.config/bb-backup/, použijí se jeho hodnoty jako prefill, jinak
    baked-in defaulty."""
    base_url = ""
    cookies_path = str(cwd / "cookies.txt")

    cfg_path = find_config_file()
    if cfg_path is not None and cfg_path.is_file():
        try:
            import tomllib

            with cfg_path.open("rb") as f:
                raw = tomllib.load(f)
            bb = raw.get("blackboard", {})
            if isinstance(bb, dict):
                base_url = str(bb.get("base_url", "") or "")
                cookies_raw = bb.get("cookies_file")
                if cookies_raw:
                    p = Path(str(cookies_raw))
                    if not p.is_absolute():
                        p = cfg_path.parent / p
                    cookies_path = str(p)
        except Exception:
            # Defenzivně — když je config.toml rozbitý, prostě použijeme
            # baked-in defaulty. Wizard pak novou hodnotu zapíše.
            pass

    return {"base_url": base_url, "cookies_path": cookies_path}


def _ensure_default_dirs(cwd: Path) -> None:
    """Vytvoří output/state/logs v cwd, ať defaulty pro prompty existují."""
    for sub in ("output", "state", "logs"):
        (cwd / sub).mkdir(parents=True, exist_ok=True)


# ---------- prompty ----------


def _prompt_base_url(default: str) -> str:
    while True:
        kwargs: dict = {"console": console}
        if default:
            kwargs["default"] = default
        value = Prompt.ask(
            "URL Blackboard instance (např. https://blackboard.priklad.cz)",
            **kwargs,
        )
        value = value.strip().rstrip("/")
        if not value:
            err_console.print("URL je prázdné.")
            continue
        if not value.startswith("https://"):
            err_console.print("URL musí začínat https://")
            continue
        return value


def _prompt_cookies_path(default: str) -> Path:
    while True:
        value = Prompt.ask(
            "Cesta k cookies.txt (Netscape formát)",
            default=default,
            console=console,
        )
        path = Path(value.strip()).expanduser()
        if not path.is_file():
            err_console.print(
                f"Soubor neexistuje: {path}\n"
                "Vyexportuj cookies z prohlížeče přes 'Get cookies.txt LOCALLY' "
                "a ulož je do tohoto souboru."
            )
            if not Confirm.ask("Zkusit jinou cestu?", default=True, console=console):
                raise typer.Exit(code=130)
            continue
        return path.resolve()


def _prompt_course_choice(
    memberships: list[tuple[Membership, Course | None]],
) -> Course:
    valid: list[Course] = [c for _, c in memberships if c is not None]
    if not valid:
        err_console.print("Žádné kurzy nenalezeny.")
        raise typer.Exit(code=0)

    table = Table(title=f"Kurzy ({len(valid)})")
    table.add_column("#", style="bold")
    table.add_column("courseId", style="cyan", no_wrap=True)
    table.add_column("name")
    table.add_column("role", style="magenta")

    role_by_id = {c.id: m.courseRoleId for m, c in memberships if c is not None}
    for idx, course in enumerate(valid, start=1):
        table.add_row(
            str(idx),
            course.courseId,
            course.name,
            role_by_id.get(course.id) or "-",
        )
    console.print(table)

    choices = [str(i) for i in range(1, len(valid) + 1)]
    selected = IntPrompt.ask(
        "Vyber kurz (číslo)",
        choices=choices,
        show_choices=False,
        console=console,
    )
    return valid[selected - 1]


def _prompt_output_dir(default: Path) -> Path:
    while True:
        value = Prompt.ask(
            "Cesta pro stažený obsah (vytvoří se, pokud neexistuje)",
            default=str(default),
            console=console,
        )
        path = Path(value.strip()).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            err_console.print(f"Nelze vytvořit adresář: {e}")
            continue
        return path.resolve()


# ---------- výpis statistik ----------


def _print_download_stats(stats, cfg: Config) -> None:
    console.print()
    console.print(
        f"[green]Hotovo:[/green] [skip] {stats.skipped}  •  "
        f"[new] {stats.new}  •  [error] {stats.errored}"
    )
    if stats.bytes_downloaded:
        console.print(f"Staženo: {format_size(stats.bytes_downloaded)}")
    if stats.errored:
        console.print(f"[yellow]Chyby v {cfg.log_dir / 'errors.log'}[/yellow]")


# ---------- entry point ----------


def run_wizard() -> None:
    cwd = Path.cwd()
    _ensure_default_dirs(cwd)
    defaults = _compute_defaults(cwd)

    console.print("[bold cyan]bb-backup wizard[/bold cyan]")
    console.print()

    base_url = _prompt_base_url(defaults["base_url"])
    cookies_path = _prompt_cookies_path(defaults["cookies_path"])

    cfg_path = cwd / "config.toml"
    try:
        save_blackboard_settings(cfg_path, base_url, str(cookies_path))
    except OSError as e:
        err_console.print(f"Nelze zapsat config.toml: {e}")
        raise typer.Exit(code=2)

    try:
        cfg = load_config(cfg_path)
    except ConfigError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)

    setup_logging(cfg)

    try:
        client = BlackboardClient(cfg)
    except BlackboardError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)

    try:
        me = client.get_me()
        console.print(f"[green]Přihlášen jako:[/green] {me.userName or me.id}")
        memberships = client.get_memberships_with_role()
    except AuthError as e:
        err_console.print(str(e))
        err_console.print()
        err_console.print(client.cookie_diagnostics())
        raise typer.Exit(code=2)
    except BlackboardError as e:
        err_console.print(f"Chyba při komunikaci s Blackboardem: {e}")
        raise typer.Exit(code=1)

    course = _prompt_course_choice(memberships)

    state_path = cfg.state_dir / course.id / "tree.json"
    console.print(f"Stahuji strom kurzu [bold]{course.name}[/bold]...")
    try:
        tree_data = walk_course(
            client,
            course.id,
            default_select_all=cfg.tui.default_select_all,
            course_name=course.name,
        )
    except AuthError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)
    except BlackboardError as e:
        err_console.print(f"Chyba: {e}")
        raise typer.Exit(code=1)

    save_tree(tree_data, state_path)

    picker = PickerApp(tree_data, state_path)
    picker.run()

    if picker.exit_action != "download":
        # Esc / quit — výběr je uložen na disku, uživatel může později
        # spustit `bb-backup download <courseId>` ručně.
        console.print(
            f"[dim]Výběr uložen v {state_path}. "
            f"Stažení můžeš spustit později přes `bb-backup download {course.id}`.[/dim]"
        )
        return

    out_dir = _prompt_output_dir(cfg.output_dir)
    if not Confirm.ask(f"Stáhnout do [bold]{out_dir}[/bold]?", default=True, console=console):
        console.print("[yellow]Stahování zrušeno.[/yellow]")
        return

    # Mutace cfg před konstrukcí Downloaderu — Downloader si výstupní cestu
    # spočítá v __init__ z cfg.output_dir.
    cfg.paths.output_dir = str(out_dir)

    dl = Downloader(client, cfg, course.id, tree_data, html_processor=process_html)
    try:
        stats = dl.run()
    except AuthError as e:
        err_console.print(str(e))
        raise typer.Exit(code=2)
    except BlackboardError as e:
        err_console.print(f"Chyba: {e}")
        raise typer.Exit(code=1)

    _print_download_stats(stats, cfg)
