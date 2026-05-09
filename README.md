# bb-backup

A command-line tool for backing up courses from Blackboard Learn LMS to your
local filesystem. It walks the course content tree, lets you pick which items
to download in an interactive TUI, and saves HTML pages plus all attachments
into a clean folder structure.

Targets **Blackboard Ultra SaaS** instances. Authentication is done by
exporting your browser session cookies — no SSO/SAML automation, no
credentials stored anywhere.

## What it downloads

- HTML body content for each item (with embedded images and linked files
  rewritten to local relative paths).
- File attachments (PDFs, slide decks, archives, etc.).

## What it skips

- Quizzes, tests, gradebooks.
- Videos and external streams (Panopto, Kaltura, YouTube embeds).
- Discussion forums, announcements, calendar.

These categories are auto-deselected in the picker but can be toggled on
manually if you want to capture their metadata anyway.

## Requirements

- Python 3.11+
- A Blackboard account with browser access
- A `cookies.txt` browser export tool:
  - Chrome: *Get cookies.txt LOCALLY*
  - Firefox: *cookies.txt*

## Install

### Option A — prebuilt binary (no Python needed)

Every push to the `release` branch triggers a GitHub Actions build that
publishes a tagged GitHub Release with both a **Windows** zip and a
**Linux** tar.gz attached. The tag is derived from the `version` field in
`pyproject.toml` — bump it before pushing, otherwise the build will fail
with a clear "tag already exists" message. The `master` branch is for
ongoing development and is not built.

1. Open the **Releases** page of this repo and grab the right archive:
   - Windows: `bb-backup-windows-x64-vX.Y.Z.zip`
   - Linux: `bb-backup-linux-x64-vX.Y.Z.tar.gz`

   (For unreleased commits, the **Actions** tab also keeps the archives
   as build artifacts for 14 days.)
2. Extract anywhere.
3. Run the binary from inside the extracted folder. No installation,
   no admin rights, no Python.
   - Windows: `bb-backup.exe`
   - Linux: `./bb-backup` (run `chmod +x bb-backup` first if needed)

The binary is built with [Nuitka](https://nuitka.net/) in standalone mode
— it bundles its own Python runtime and all dependencies. The whole folder
is what runs; don't move `bb-backup.exe` out of it on its own.

> Heads up: the binary is **not code-signed**, so SmartScreen / antivirus
> may warn the first time. If you don't trust the prebuilt artifact, build
> from source (Option B).

### Option B — from source

```bash
git clone <repo-url> bb-backup
cd bb-backup
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -e .
```

### Option C — local wrapper (no PATH, Windows)

After Option B, you can use the included `bb.bat` to call the tool from any
directory without activating the venv or adding it to `PATH`. The wrapper
resolves the venv next to itself and forwards all arguments.

```cmd
cd C:\path\to\my-backups
C:\path\to\bb-backup-repo\bb.bat
```

`config.toml`, `cookies.txt`, `output/`, `state/`, and `logs/` are read and
written relative to the current working directory, so `cd` to your backup
folder before calling the wrapper.

## Quick start (wizard)

The fastest way is the interactive wizard — it walks you through everything
in one run.

1. **Export your browser cookies** while logged in to your Blackboard
   instance. Use the *Get cookies.txt LOCALLY* (Chrome) or *cookies.txt*
   (Firefox) extension and save the file somewhere — typically as
   `cookies.txt` in the directory where you want backups to go.

2. **Run the wizard:**

   ```bash
   bb-backup
   ```

   The wizard will:
   - prompt for your Blackboard base URL (e.g. `https://blackboard.example.com`)
   - prompt for the path to your `cookies.txt`
   - save those values to `config.toml` so the next run pre-fills them
   - log in, list your enrolled courses, and let you pick one by number
   - fetch the course tree and open the picker TUI
   - on `Ctrl+D`, ask where to save and start the download

That is the full flow. No other commands needed.

## TUI keys

Used by both the wizard and the standalone `bb-backup pick` command.

| Key      | Action                                          |
|----------|-------------------------------------------------|
| `Enter`  | Expand / collapse the current node              |
| `Space`  | Toggle selection of the current item            |
| `Ctrl+A` | Select all (skipped items stay deselected)      |
| `Ctrl+C` | Deselect all                                    |
| `Ctrl+S` | Save the current selection to `tree.json`       |
| `Ctrl+D` | Save and start downloading (wizard flow only)   |
| `Esc`    | Exit (asks to confirm if there are unsaved edits) |

## Output layout

```
output/
└── <Course Name>/
    └── <Folder>/
        └── <Item title>/
            ├── index.html      # HTML body, with rewritten asset/link URLs
            ├── _assets/        # images and embeds referenced from index.html
            │   └── diagram.png
            └── lecture-01.pdf  # original attachments

state/
└── <courseId>/
    ├── tree.json       # course content tree + your selection
    └── manifest.json   # download log (sha256, size, timestamp)

logs/
├── bb-backup.log       # rolling log
└── errors.log          # tracebacks for failed items (download continues)
```

`download` is **idempotent** — re-running skips files already present in the
manifest with matching size, and only fetches what's new or changed.

## Subcommands (advanced / scripting)

The wizard wraps these. You can call them directly when scripting or
re-running individual steps.

| Command                                | Purpose                                                       |
|----------------------------------------|---------------------------------------------------------------|
| `bb-backup`                            | Interactive wizard (recommended).                             |
| `bb-backup wizard`                     | Same as above, explicit alias.                                |
| `bb-backup init`                       | Drop a commented `config.example.toml` and `.gitignore` here. |
| `bb-backup probe [--debug]`            | Verify auth and list available courses.                       |
| `bb-backup tree <courseId> [--force]`  | Fetch the course tree to `state/<courseId>/tree.json`.        |
| `bb-backup pick <courseId>`            | Open the TUI to edit the selection in `tree.json`.            |
| `bb-backup download <courseId>`        | Download selected items to `output/<course-name>/`.           |
| `bb-backup version`                    | Print version.                                                |

## Configuration

`config.toml` lives in the current directory (fallback:
`~/.config/bb-backup/config.toml`). The wizard creates and updates it for
you. Run `bb-backup init` to drop a fully commented `config.example.toml`
showing every option with defaults.

Minimal config:

```toml
[blackboard]
base_url = "https://blackboard.example.com"
cookies_file = "cookies.txt"
```

Other tunables (paths, request delay, retries, timeout, log level) all have
sensible defaults — see `config.example.toml`.

## Troubleshooting

| Problem | Fix |
|---|---|
| Authentication failed (401) | Cookies expired. Re-export from the browser and re-run. |
| `config.toml` not found | Run the wizard, or `bb-backup init` and fill in `config.toml` manually. |
| Cookie file not found | The path in `[blackboard].cookies_file` doesn't exist. Re-export or fix the path. |
| Rate-limited (429) | Increase `download.request_delay_ms` in `config.toml` (try 500–1000). |
| `tree.json already exists` | Use `--force` on `tree`, but note this discards your saved selection. |
| Download crashed mid-way | Just re-run `bb-backup download <courseId>` — the manifest skips finished files. |
| Body content missing | Some `contentHandler` types have no body, only attachments. That's normal. |

## Security notes

- `cookies.txt` contains live session tokens. Treat it like a password —
  never commit it, never share it.
- The default `.gitignore` shipped with `bb-backup init` excludes
  `config.toml`, `cookies.txt`, `output/`, `state/`, and `logs/`.
- The tool only reads from Blackboard. It never modifies course content,
  posts, or grades.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

Tests are mocked (`responses` library) — they don't touch a real Blackboard
instance.

## License

MIT
