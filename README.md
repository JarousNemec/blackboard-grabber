# bb-backup

Command-line tool for backing up **Blackboard Ultra SaaS** courses to your
local filesystem. Pick what to download in an interactive TUI, get HTML
pages and all attachments in a clean folder tree.

Auth is via your exported browser cookies — no credentials stored, no
SSO/SAML automation.

## What gets downloaded

- HTML body of each item (with embedded images and links rewritten to local paths).
- File attachments (PDFs, slides, archives, …).

Skipped by default: quizzes, gradebooks, videos (Panopto, Kaltura, YouTube),
discussion forums, announcements. You can toggle them on in the picker.

## Install

### Prebuilt binary (recommended)

No Python needed. Grab the archive for your OS from the
[Releases page](../../releases) and extract it anywhere:

- Windows: `bb-backup-windows-x64-vX.Y.Z.zip` → run `bb-backup.exe`
- Linux:   `bb-backup-linux-x64-vX.Y.Z.tar.gz` → run `./bb-backup`

The binary is a self-contained folder (Nuitka standalone). Don't move
`bb-backup.exe` out of it.

> The binary is **not code-signed**, so SmartScreen / antivirus may warn the
> first time. If you don't trust it, build from source below.

### From source

Requires Python 3.11+.

```bash
git clone <repo-url> bb-backup
cd bb-backup
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
pip install -e .
```

> **Windows tip:** to call the tool from any directory without activating
> the venv, use the included `bb.bat` wrapper — `cd` into your backup
> folder and run `C:\path\to\bb-backup-repo\bb.bat`.

## Usage

### 1. Export your browser cookies

While logged in to Blackboard, export cookies using:

- Chrome: *Get cookies.txt LOCALLY* extension
- Firefox: *cookies.txt* extension

Save the file (e.g. as `cookies.txt`) into the directory where you want
your backups to live. Cookies usually last 30 min – 8 h; on 401 mid-run
just re-export and run download again.

### 2. Run the wizard

```bash
cd /path/to/your/backup-folder
bb-backup
```

The wizard will:

1. Ask for your Blackboard base URL (e.g. `https://blackboard.example.com`)
   and the path to `cookies.txt`. Both get saved to `config.toml` for
   next time.
2. List your courses; pick one by number.
3. Fetch the content tree and open the picker TUI.
4. On `Ctrl+D`, start the download.

That's it.

## TUI keys

| Key      | Action                                          |
|----------|-------------------------------------------------|
| `Enter`  | Expand / collapse node                          |
| `Space`  | Toggle selection                                |
| `Ctrl+A` | Select all (skipped items stay deselected)      |
| `Ctrl+C` | Deselect all                                    |
| `Ctrl+S` | Save selection to `tree.json`                   |
| `Ctrl+D` | Save and start download (wizard only)           |
| `Esc`    | Exit (asks to confirm if there are unsaved edits) |

## Output layout

```
output/
└── <Course Name>/
    └── <Folder>/
        └── <Item title>/
            ├── index.html      # HTML body, asset/link URLs rewritten
            ├── _assets/        # images and embeds referenced from index.html
            └── lecture-01.pdf  # original attachments

state/
└── <courseId>/
    ├── tree.json       # course tree + your selection
    └── manifest.json   # download log (sha256, size, timestamp)

logs/
├── bb-backup.log
└── errors.log          # tracebacks for failed items (download continues)
```

`download` is **idempotent** — re-running skips files already in the
manifest, only fetches what's new.

## Configuration

`config.toml` lives in the current directory (fallback:
`~/.config/bb-backup/config.toml`). Wizard creates and updates it. For a
fully commented template run `bb-backup init`.

Minimal:

```toml
[blackboard]
base_url = "https://blackboard.example.com"
cookies_file = "cookies.txt"
```

Other tunables (request delay, retries, timeout, log level, paths) have
sensible defaults — see `config.example.toml`.

## Subcommands (scripting / re-runs)

The wizard wraps these; you can call them directly.

| Command                                | Purpose                                                |
|----------------------------------------|--------------------------------------------------------|
| `bb-backup`                            | Interactive wizard (recommended).                      |
| `bb-backup init`                       | Drop `config.example.toml` and `.gitignore` here.      |
| `bb-backup probe [--debug]`            | Verify auth, list courses.                             |
| `bb-backup tree <courseId> [--force]`  | Fetch tree to `state/<courseId>/tree.json`.            |
| `bb-backup pick <courseId>`            | Open TUI to edit the selection in `tree.json`.         |
| `bb-backup download <courseId>`        | Download selected items to `output/<course-name>/`.    |
| `bb-backup version`                    | Print version.                                         |

## Troubleshooting

| Problem                       | Fix                                                                       |
|-------------------------------|---------------------------------------------------------------------------|
| Authentication failed (401)   | Cookies expired. Re-export and re-run.                                    |
| `config.toml` not found       | Run wizard, or `bb-backup init` and edit it.                              |
| Cookie file not found         | Path in `[blackboard].cookies_file` is wrong / file moved.                |
| Rate-limited (429)            | Increase `download.request_delay_ms` (try 500–1000).                      |
| `tree.json already exists`    | Use `tree --force` (discards your saved selection).                       |
| Download crashed mid-way      | Just re-run `download` — the manifest skips finished files.               |
| Body content missing          | Some `contentHandler` types have no body, only attachments. Normal.       |

## Security

- `cookies.txt` contains live session tokens — treat it like a password.
  Never commit it, never share it.
- `bb-backup init` ships a `.gitignore` excluding `config.toml`,
  `cookies.txt`, `output/`, `state/`, `logs/`.
- The tool only reads from Blackboard — never modifies content, posts, or grades.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

Tests are mocked (`responses`) — they don't hit a live Blackboard.

## License

MIT
