# Omnify — v1 status and Notes handoff

Updated: 2026-11-11. This file is the handoff source of truth for the
next session. The student report for practical works is in
`ПРАКТИЧНІ_РОБОТИ.md`; the public-facing project guide is `README.md`.

## v1 state

Omnify v1 is ready for daily use. The shell, dashboard, calculator,
calendar, timer, stopwatch, alarm clock, task manager, Settings,
themes, EN↔UA localization, the standalone browser and the Notes
module are implemented. The full headless suite is green: **453 tests**.

Notes can be copied one-way to a folder or merged two-way with another
notes folder by stable id; deletions are not propagated by the two-way
merge. Not built yet: cloud/native phone sync with a hosted account,
real-time collaboration, and optional later tools such as a file manager
or mail.

## Run and install

```sh
cd /home/sillycat/projects/omnify
python3 -m omnify             # run from the project
./install.sh                  # install/update launchers and app-menu entries
```

Installed commands:

- `omnify` — the single Omnify process; owns timers, alarms and tray.
- `omnify --module <id>` — open/switch an already running instance.
- `omnify-browser [URL...]` — standalone managed LibreWolf/Firefox.
- `omnify-browser --tor [URL...]` — standalone Tor Browser.

Installed module ids: `dashboard`, `calculator`, `browser`, `calendar`,
`timer`, `stopwatch`, `alarm`, `taskmanager`, `notes`, `settings`.

Desktop entries are installed under `~/.local/share/applications/`,
including one entry per module. Autostart resumes timers/stopwatch after
login.

## Features

- **Dashboard** — greeting, clock, quick actions (new note and today's
  journal), a Notes summary row, background status and module tiles.
- **Calculator** — scientific, exact/complex arithmetic, matrices,
  calculus, units, history, graphing.
- **Calendar** — week/day/month, events, layers, recurrence, reminders,
  search, stats and habits.
- **Timer / stopwatch** — multiple persistent timers, presets, background
  operation, sounds, notifications, key/click/at triggers, laps.
- **Alarm clock** — persistent alarms, weekday repeats, one-shot alarms,
  snooze, custom sound, notifications and ringing banner.
- **Task manager** — processes, performance, services, startup, users,
  details and safe kill flows.
- **Settings** — EN↔UA switching (English default), theme mode, six
  accents, custom colours, fonts, radius, background image, sidebar
  side/width, theme import/export and data reset.

The default look includes the preferred **left sidebar** and subtle
accent circles drawn by the theme itself (no image file required).
User background images replace the circles when selected.

## Localization

`omnify/core/i18n.py` discovers `omnify/locales/<code>.py` modules.
`omnify/locales/uk.py` holds the Ukrainian catalogue. The English source
string is the key; unknown strings fall back to English. To add a
language, add one locale file with `NAME` and `TRANSLATIONS`.

All visible static strings were audited; dynamic labels are translated
at creation or on `retranslate()`. Known non-app English text: browser
names/user content and untranslated strings inside third-party websites
or Firefox components.

## Browser

Primary browser: portable **LibreWolf AppImage** with a managed profile at
`~/.local/share/omnify/browser/profile`. Firefox is a fallback. The
profile is hardened with tracking/cookie/fingerprinting/HTTPS
protections; **Omnify Shields** is a local extension that shows blocked
hosts per tab and supports per-site pause. Private windows and a
maximum-privacy **Tor Browser** launcher are available. Tor Browser is
installed at `~/.local/share/omnify/browser/tor-browser` and its UI is
Ukrainian.

The managed profile also disables RFP letterboxing margins (they looked
like grey strips on a maximized window; the rest of RFP stays active) and
the "close all tabs" confirmation (the old default forced the prompt even
after ticking "do not ask again"). If you see those old behaviours, an
old LibreWolf process is still running: close every LibreWolf window and
reopen it from Omnify Browser or the Omnify Browser menu entry.

Passwords use Firefox's local encrypted store; a primary password is
strongly recommended. Do not claim that raw Chromium profile copying
imports passwords — Chromium encrypts them with the system keyring.

### Migrating from Brave

Open **Omnify → Browser → Migration → Open built-in importer**, then in
LibreWolf choose **Import Browser Data → Brave** and tick every category.
Firefox decrypts Brave passwords and cookies through the system keyring;
Omnify never receives the secrets. A Firefox-family source can instead be
copied directly with the migration button after closing the managed
browser; the tool creates a timestamped backup and deliberately does not
copy the source `prefs.js` (so source security settings cannot weaken the
managed profile).

Brave was detected at `~/.config/BraveSoftware/Brave-Browser/Default`.
Direct Chromium copying is intentionally disabled; use the built-in
importer for the complete transfer.

## Architecture constraints

- Python 3.14 + `gi`/GTK 4; standard library only, no pip packages.
- Only `omnify/app/shell.py` imports `gi` at module level (pinned GTK/GDK).
  Modules import GTK lazily inside functions and are headless-safe.
- Pure models hold testable behaviour in `tests/`; GTK views stay thin.
- Theme colours come from `omnify/core/theme.py`; widgets do not hard-code
  colours. Degrade gracefully when optional system tools are absent.
- `omnify/modules/__init__.py` is the module registry. A module implements
  `id`, `name`, `icon`, `description`, `group`, `build_view()`, and optional
  `on_show()`, `on_hide()`, `retranslate()`, `save_state()`.
- Desktop module entries call `omnify --module <id>`; a second launch
  activates the existing process so timers/alarms are never duplicated.

## Notes module

The Notes module is implemented:

- each note is a Markdown file under `notes/` in the project root;
- notes have YAML-style front matter with a stable id, title, tags and
  timestamp;
- notes can live in folders under `notes/`; the sidebar filters by folder,
  can create folders, rename them, and delete one (its notes move to
  **Unfiled** rather than being deleted); destructive folder operations
  clean up empty nested directories;
- list/search, create, select, edit and delete notes; pinned notes sort first;
  the list shows a short excerpt from the first meaningful Markdown line;
- **Move to Trash** is a recoverable soft delete: the Markdown file and its
  folder path are moved to `notes/.trash/`, images stay in `assets/<id>/`,
  and the **Trash…** dialog can restore a note, delete it permanently or empty
  the whole trash;
- **Duplicate note** creates an independent copy with a new id, preserving
  the folder, tags and pin state and copying/rewriting its image assets;
- **New from template…** offers built-in daily-plan, meeting, study and
  weekly-review notes; **Today's journal** opens or creates a dated note in
  the stable `Journal` folder with a plan/notes/summary scaffold;
- **Version history** keeps the last 20 snapshots per note under
  `notes/.history/<id>/`; a snapshot is created before an edit at most once
  every five minutes, plus on demand from **Save version**. The history
  dialog previews a version, restores it with a safety snapshot, deletes one
  version or clears the whole history; history is removed with the note;
- **Find and replace** (`Ctrl+H`) works on the current note, supports match
  case, next/previous wrap-around, single replacement and replace-all;
  replacement text is treated literally (no regex/backslash surprises);
- tags are edited as a comma-separated line and are searchable with `#tag`;
  the tag menu filters by tag and the field offers known-tag completion;
  **Manage tags…** renames or deletes a tag across all notes at once, with
  case-insensitive matching and duplicate prevention;
- a formatting toolbar inserts bold, italic, strike-through, headings,
  lists, quotes, code and links; keyboard shortcuts cover save, search,
  bold, italic and link insertion;
- checklists and tables can be inserted with toolbar buttons; Markdown
  checklists and pipe tables are rendered in preview and in exports;
- **Preview** renders headings, paragraphs, lists, checklists, tables, code,
  links, images and tags without WebKit; checklist boxes are clickable;
- export to standalone HTML (images embedded as base64), PDF through
  cairo/Pango, a portable Markdown folder with copied assets, and all notes
  as an HTML bundle with `index.html` or a Markdown bundle with `index.md`;
- Markdown import copies external `.md`/`.markdown` files into the chosen
  folder, preserves title/tags/pinned metadata and allocates a fresh id on
  collision without touching the source;
- autosave with a debounce timer; the previous note is flushed when switching;
  last selected note and folder filter restored on restart;
- images are copied to `notes/assets/<note-id>/` and inserted as relative
  Markdown links (`![alt](assets/...)`);
- local images are rendered inline in the editor; the Markdown link is
  reconstructed when the note is saved;
- Ctrl+V inserts a clipboard image when it contains PNG/JPEG/WebP data;
- ZIP backups can be created manually or automatically once per day; backups
  are kept under `~/.local/share/omnify/backups/` (last 10 retained);
- **Sync notes to folder…** copies the notes tree one-way to a folder chosen
  by the user (for example a Syncthing/Dropbox folder);
- **Two-way sync with folder…** merges notes by stable id and lets the newer
  `updated_at` win; it creates a safety backup first and does not propagate
  deletions;
- **Restore backup…** merges a ZIP backup into the current notes and creates
  a safety backup first;
- legacy notes from `notes.json` are migrated automatically on first launch;
- `notes/` is ignored by Git;
- model, file store and renderer covered by `tests/test_notes_engine.py`,
  `tests/test_notes_store.py`, `tests/test_notes_ui_helpers.py` and
  `tests/test_notes_render.py`;
- registered in `omnify/modules/__init__.py`.

## UI artifacts

- `docs/wireframes/dashboard.svg` — dashboard layout with quick actions,
  glance card, recent notes and module tiles;
- `docs/wireframes/notes.svg` — note list, search, folders, tags, toolbar,
  Markdown editor and preview;
- `docs/wireframes/README.md` — rationale and editing notes; the SVGs can be
  imported into Figma without adding it as a runtime dependency.

## Verification checklist

- `python3 -m py_compile $(find omnify tests -name '*.py')`
- `python3 -m unittest discover -s tests` → **453 OK**
- `./install.sh` → launchers + app-menu entries created
- `omnify --module calculator|browser|alarm|settings|...` switches the
  running instance
- `omnify-browser` opens LibreWolf maximized; Tor entry opens Tor Browser
  in Ukrainian
- Browser migration page detects Brave and opens the Firefox importer

Known cosmetic limitation: this GTK build emits harmless slider
`min width/height -2` warnings on startup.
