# Omnify — v1 status and Notes handoff

Updated: 2026-11-05. This file is the handoff source of truth for the
next session.

## v1 state

Omnify v1 is ready for daily use. The shell, dashboard, calculator,
calendar, timer, stopwatch, alarm clock, task manager, Settings,
themes, EN↔UA localization, the standalone browser and the basic Notes
module are implemented. The full headless suite is green: **410 tests**.

Not built yet: native phone sync and optional later tools such as a file
manager or mail.

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

- **Dashboard** — greeting, clock, quick actions, background status and
  module tiles.
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

The basic Notes module is implemented:

- list/search, create, select and edit notes;
- autosave through `ctx.storage` with a debounce timer;
- last selected note restored on restart;
- model covered by `tests/test_notes_engine.py`;
- registered in `omnify/modules/__init__.py`.

## Verification checklist

- `python3 -m py_compile $(find omnify tests -name '*.py')`
- `python3 -m unittest discover -s tests` → **410 OK**
- `./install.sh` → launchers + app-menu entries created
- `omnify --module calculator|browser|alarm|settings|...` switches the
  running instance
- `omnify-browser` opens LibreWolf maximized; Tor entry opens Tor Browser
  in Ukrainian
- Browser migration page detects Brave and opens the Firefox importer

Known cosmetic limitation: this GTK build emits harmless slider
`min width/height -2` warnings on startup.
