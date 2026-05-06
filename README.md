# bb-backup

Lokální záloha kurzů z Blackboard Learn LMS.

## Quickstart

```bash
# 1. Instalace (uvnitř .venv)
pip install -e .

# 2. Inicializuj projekt v adresáři, kde chceš mít zálohy
bb-backup init

# 3. Vyplň config
cp config.example.toml config.toml
# Otevři config.toml a nastav blackboard.base_url

# 4. Vyexportuj cookies z přihlášeného prohlížeče
# - Chrome: rozšíření "Get cookies.txt LOCALLY"
# - Firefox: rozšíření "cookies.txt"
# - Klikni na rozšíření na otevřené stránce Blackboardu, Export → ulož jako cookies.txt

# 5. Ověř přihlášení a vypiš seznam kurzů
bb-backup probe
```

## Subkomandy

| Příkaz | Popis |
|---|---|
| `bb-backup init` | Vytvoří `config.example.toml` a `.gitignore`. |
| `bb-backup probe` | Vypíše seznam kurzů, kam máš přístup. |
| `bb-backup tree <courseId>` | Stáhne strom obsahu kurzu do `state/<courseId>/tree.json`. |
| `bb-backup pick <courseId>` | TUI pro výběr položek ke stažení (checkboxy). |
| `bb-backup download <courseId>` | Stáhne vybrané položky do `output/<courseId>/...`. |

`download` je idempotentní — opakované spuštění přeskočí, co už je staženo.

## Co tool **ne**stahuje

- Kvízy a testy
- Videa (Panopto, Kaltura, YouTube embedy)
- Diskuzní fóra, oznámení, kalendář

## Co se ukládá kam

```
output/
└── <Název kurzu>/
    └── Týden 1 — Úvod/
        └── Prezentace z přednášky/
            ├── index.html         # body content (přepsané embedy)
            ├── _assets/           # obrázky a embedované soubory z body
            │   └── diagram.png
            └── prednaska01.pptx   # přílohy

state/
└── <courseId>/
    ├── tree.json       # strom obsahu kurzu + výběr (selected: true/false)
    └── manifest.json   # log staženého (sha256, size, downloaded_at)

logs/
├── bb-backup.log       # rolling log podle level
└── errors.log          # stack traces chyb během download
```

## Troubleshooting

| Problém | Řešení |
|---|---|
| `Autentizace selhala` | Cookies vypršely — vyexportuj je z prohlížeče znovu (rozšíření Get cookies.txt LOCALLY). |
| `config.toml nenalezen` | Spusť `bb-backup init` a vyplň config. |
| `Soubor s cookies neexistuje` | Vyexportuj cookies z prohlížeče do souboru, jehož cestu jsi zadal v `cookies_file`. |
| Rate limit / 429 | Zvyš `download.request_delay_ms` v `config.toml` (např. 500–1000). |
| `tree.json už existuje` | Při novém `tree` použij `--force`, ale POZOR: zahodí ručně odškrtaný výběr. |
| Některé položky jsou skipnuté | Kvízy, testy, videa, fóra — viz [Co tool nestahuje](#co-tool-nestahuje). V TUI je můžeš ručně zaškrtnout. |
| Body content chybí | Některé `contentHandler` typy nemají body — to je v pořádku, přílohy se stáhnou. |
| Download spadl v půlce | Spusť `bb-backup download <courseId>` znovu — pokračuje, kde skončil. |

## Klávesy v TUI (`bb-backup pick`)

| Klávesa | Akce |
|---|---|
| `Space` | Toggle vybrané položky |
| `Shift+Space` | Toggle rekurzivně včetně dětí |
| `a` | Vybrat vše |
| `n` | Odznačit vše |
| `s` | Uložit zpět do `tree.json` |
| `q` | Konec (s konfirmací při neuložených změnách) |

## Vývoj

```bash
pip install -e ".[dev]"
pytest
```
