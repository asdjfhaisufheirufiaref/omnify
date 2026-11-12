# Omnify UI wireframes

These SVG files are lightweight, editable wireframes for the practical
UI-prototype requirement. They are not a runtime dependency and do not
require a Figma account, a browser or network access.

| File | Screen | What it shows |
| --- | --- | --- |
| [`dashboard.svg`](dashboard.svg) | Dashboard | sidebar, greeting, clock, quick actions, glance card, recent notes and module tiles |
| [`notes.svg`](notes.svg) | Notes | searchable note list, folders, tags, Markdown toolbar, editor, metadata and preview |

## Why SVG?

- They open in any browser, Inkscape, Krita, Illustrator or Figma (via
  **File → Import**).
- They stay small and diff-friendly in Git.
- They mirror the running GTK prototype instead of describing a separate
  application.

The actual product is the running application; these files are supporting
artifacts for the interface section of the report. For the full screen
inventory and interaction scenarios, see
[`ПРАКТИЧНІ_РОБОТИ.md`](../../ПРАКТИЧНІ_РОБОТИ.md).

## Editing

The files use plain SVG 1.1 with a small embedded stylesheet. Text, colours
and card positions can be edited with any text editor. The sidebar, card
and accent styles are shared by both files, so a design change can be
repeated consistently.
