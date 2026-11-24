"""Headless tests for the File Explorer model and helpers.

The model accepts injectable filesystem readers, so most tests use temporary
directories and never touch the user's home, while the default XDG trash
implementation is exercised inside a throwaway trash directory.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.modules.files import (  # noqa: E402
    FileEntry,
    FileExplorer,
    FileExplorerModule,
    NavigationHistory,
    breadcrumb_parts,
    disk_usage,
    filter_entries,
    find_terminal,
    format_file_size,
    format_modified,
    human_kind,
    parse_user_dirs,
    preview_kind,
    resolve_location,
    sort_entries,
    terminal_argv,
    unique_child_name,
    validate_name,
)
from omnify.core.module_context import ModuleContext  # noqa: E402
from omnify.core.storage import Storage  # noqa: E402


class FakeExists:
    def __init__(self, present):
        self.present = set(present)

    def __call__(self, path):
        return path in self.present


def entry(
    name: str,
    is_dir: bool = False,
    size: int = 0,
    modified: float = 0.0,
    kind: str = None,
) -> FileEntry:
    return FileEntry(
        name=name,
        path=f"/tmp/{name}",
        is_dir=is_dir,
        is_symlink=False,
        size=size,
        modified=modified,
        mode=0o644,
        kind=kind or human_kind(name, is_dir),
    )


class HelperTests(unittest.TestCase):
    def test_format_file_size_units(self):
        self.assertEqual(format_file_size(0), "0 B")
        self.assertEqual(format_file_size(1536), "1.5 KiB")
        self.assertEqual(format_file_size(2 * 1024 ** 3), "2.0 GiB")
        self.assertEqual(format_file_size(None), "—")

    def test_format_modified(self):
        self.assertEqual(format_modified(None), "—")
        rendered = format_modified(0)
        self.assertRegex(rendered, r"^\d{4}-\d\d-\d\d \d\d:\d\d$")

    def test_human_kind(self):
        self.assertEqual(human_kind("Documents", is_dir=True), "Folder")
        self.assertEqual(human_kind("note.md"), "Markdown document")
        self.assertEqual(human_kind("photo.PNG"), "Image")
        self.assertEqual(human_kind("track.flac"), "Audio")
        self.assertEqual(human_kind("archive.tar.gz"), "Archive")
        self.assertEqual(human_kind("script.sh"), "Script")
        self.assertEqual(human_kind("runme", mode=0o755), "Executable")
        self.assertEqual(human_kind("unknown.xyz"), "File")
        self.assertEqual(
            human_kind("link", is_symlink=True), "Symbolic link"
        )
        self.assertEqual(
            human_kind("link", is_dir=True, is_symlink=True), "Folder link"
        )

    def test_parse_user_dirs_substitutes_home_and_ignores_junk(self):
        text = (
            "# comment\n"
            'XDG_DESKTOP_DIR="$HOME/Desktop"\n'
            'XDG_DOWNLOAD_DIR="$HOME/Завантаження"\n'
            'XDG_DOCUMENTS_DIR="~/Docs"\n'
            'XDG_UNKNOWN_DIR="/nope"\n'
            "BROKEN\n"
        )
        data = parse_user_dirs(text, "/home/alice")
        self.assertEqual(data["desktop"], "/home/alice/Desktop")
        self.assertEqual(data["downloads"], "/home/alice/Завантаження")
        self.assertEqual(data["documents"], "/home/alice/Docs")
        self.assertNotIn("unknown", data)

    def test_resolve_location(self):
        base = "/home/alice/Documents"
        home = "/home/alice"
        self.assertEqual(resolve_location("", base, home), base)
        self.assertEqual(resolve_location("sub", base, home), base + "/sub")
        self.assertEqual(resolve_location("~/Music", base, home), home + "/Music")
        self.assertEqual(resolve_location("/etc", base, home), "/etc")
        self.assertEqual(
            resolve_location("file:///tmp/a%20b", base, home), "/tmp/a b"
        )

    def test_validate_name(self):
        self.assertEqual(validate_name("  report.txt  "), "report.txt")
        for bad in ("", "   ", ".", "..", "a/b"):
            with self.assertRaises(ValueError):
                validate_name(bad)

    def test_unique_child_name(self):
        exists = FakeExists({"/d/report.txt", "/d/report (copy).txt"})
        self.assertEqual(
            unique_child_name("/d", "report.txt", exists),
            "report (copy 2).txt",
        )
        self.assertEqual(
            unique_child_name("/d", "new.txt", exists), "new.txt"
        )

    def test_filter_entries_is_case_insensitive(self):
        entries = [entry("Report.txt"), entry("photo.png"), entry("REPORT.md")]
        self.assertEqual(
            [item.name for item in filter_entries(entries, "report")],
            ["Report.txt", "REPORT.md"],
        )
        self.assertEqual(filter_entries(entries, "  "), entries)

    def test_sort_entries_folders_first(self):
        entries = [
            entry("b.txt", size=2),
            entry("A", is_dir=True),
            entry("a.txt", size=3),
            entry("b", is_dir=True),
        ]
        names = [item.name for item in sort_entries(entries, "name")]
        self.assertEqual(names, ["A", "b", "a.txt", "b.txt"])
        names = [item.name for item in sort_entries(entries, "size", True)]
        self.assertEqual(names, ["A", "b", "a.txt", "b.txt"])

    def test_preview_kind(self):
        self.assertEqual(preview_kind("a.png"), "image")
        self.assertEqual(preview_kind("a.md"), "text")
        self.assertEqual(preview_kind("a.bin"), "none")

    def test_breadcrumb_parts_inside_home(self):
        home = "/home/alice"
        self.assertEqual(breadcrumb_parts(home, home), [("Home", home)])
        self.assertEqual(
            breadcrumb_parts("/home/alice/Projects/omnify", home),
            [
                ("Home", home),
                ("Projects", "/home/alice/Projects"),
                ("omnify", "/home/alice/Projects/omnify"),
            ],
        )

    def test_breadcrumb_parts_outside_home(self):
        self.assertEqual(
            breadcrumb_parts("/etc/nginx", "/home/alice"),
            [
                ("File System", "/"),
                ("etc", "/etc"),
                ("nginx", "/etc/nginx"),
            ],
        )
        self.assertEqual(breadcrumb_parts("/", "/home/alice"), [("File System", "/")])

    def test_terminal_argv_per_emulator(self):
        self.assertEqual(
            terminal_argv("/usr/bin/gnome-terminal", "/tmp"),
            ["/usr/bin/gnome-terminal", "--working-directory", "/tmp"],
        )
        self.assertEqual(
            terminal_argv("/usr/bin/kitty", "/tmp"),
            ["/usr/bin/kitty", "--directory", "/tmp"],
        )
        self.assertEqual(
            terminal_argv("/usr/bin/wezterm", "/tmp"),
            ["/usr/bin/wezterm", "start", "--cwd", "/tmp"],
        )
        self.assertEqual(
            terminal_argv("/usr/bin/unknown-term", "/tmp"),
            ["/usr/bin/unknown-term"],
        )

    def test_find_terminal_returns_first_available(self):
        installed = {"konsole": "/usr/bin/konsole"}
        self.assertEqual(
            find_terminal(lambda name: installed.get(name)),
            "/usr/bin/konsole",
        )
        self.assertIsNone(find_terminal(lambda _name: None))

    def test_find_terminal_survives_lookup_errors(self):
        def broken(name):
            raise OSError(name)

        self.assertIsNone(find_terminal(broken))

    def test_disk_usage_math_and_errors(self):
        class Stats:
            f_blocks = 100
            f_bavail = 40
            f_frsize = 512

        self.assertEqual(disk_usage("/", lambda _p: Stats()), (51200, 30720, 20480))

        def broken(_path):
            raise OSError("no mount")

        self.assertIsNone(disk_usage("/missing", broken))

        class Empty:
            f_blocks = 0
            f_bavail = 0
            f_frsize = 4096

        self.assertIsNone(disk_usage("/", lambda _p: Empty()))


class NavigationHistoryTests(unittest.TestCase):
    def test_visit_back_forward_and_branching(self):
        history = NavigationHistory("/a")
        history.visit("/a")
        history.visit("/b")
        history.visit("/c")
        self.assertTrue(history.can_back)
        self.assertFalse(history.can_forward)
        self.assertEqual(history.back(), "/b")
        self.assertEqual(history.forward(), "/c")
        self.assertEqual(history.back(), "/b")
        history.visit("/d")
        self.assertFalse(history.can_forward)
        self.assertEqual(history.current, "/d")

    def test_replace_current_without_history_step(self):
        history = NavigationHistory()
        history.replace_current("/a")
        history.replace_current("/b")
        self.assertEqual(history.current, "/b")
        self.assertFalse(history.can_back)
        self.assertFalse(history.can_forward)

    def test_back_at_start_returns_none(self):
        self.assertIsNone(NavigationHistory("/a").back())
        self.assertIsNone(NavigationHistory().forward())


class FileExplorerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.folder = self.home / "Project"
        self.folder.mkdir()
        (self.folder / "child.txt").write_text("child", encoding="utf-8")
        (self.home / "notes.txt").write_text("hello world", encoding="utf-8")
        (self.home / "report.md").write_text("# Report\n", encoding="utf-8")
        (self.home / ".hidden").write_text("secret", encoding="utf-8")
        (self.home / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (self.home / "binary.bin").write_bytes(b"abc\0def")
        self.model = FileExplorer(home=str(self.home))

    def test_list_entries_hides_dotfiles_by_default(self):
        entries = self.model.list_entries(str(self.home))
        names = [item.name for item in entries]
        self.assertIn("notes.txt", names)
        self.assertIn("Project", names)
        self.assertNotIn(".hidden", names)

    def test_list_entries_show_hidden_and_metadata(self):
        entries = {
            item.name: item
            for item in self.model.list_entries(str(self.home), show_hidden=True)
        }
        self.assertIn(".hidden", entries)
        self.assertTrue(entries["Project"].is_dir)
        self.assertEqual(entries["Project"].kind, "Folder")
        self.assertEqual(entries["notes.txt"].size, 11)
        self.assertEqual(entries["notes.txt"].kind, "Text file")
        self.assertEqual(entries["image.png"].kind, "Image")

    def test_list_entries_skips_vanished_entries(self):
        class Vanishing:
            def __call__(self, path):
                if path.endswith("notes.txt"):
                    raise FileNotFoundError(path)
                return os.listdir(path)

        def stat_missing(path):
            if path.endswith("notes.txt"):
                raise FileNotFoundError(path)
            return os.stat(path)

        model = FileExplorer(
            home=str(self.home), list_dir=Vanishing(), stat=stat_missing
        )
        names = [item.name for item in model.list_entries(str(self.home))]
        self.assertNotIn("notes.txt", names)
        self.assertIn("report.md", names)

    def test_places_uses_xdg_user_dirs(self):
        downloads = self.home / "Downloads"
        downloads.mkdir()
        config = self.home / ".config"
        config.mkdir()
        (config / "user-dirs.dirs").write_text(
            f'XDG_DOWNLOAD_DIR="{downloads}"\nXDG_MUSIC_DIR="{self.home}/NoMusic"\n',
            encoding="utf-8",
        )
        places = self.model.places()
        by_key = {place.key: place for place in places}
        self.assertEqual(by_key["home"].path, str(self.home))
        self.assertEqual(by_key["downloads"].path, str(downloads))
        self.assertNotIn("music", by_key)  # missing directories are hidden
        self.assertEqual(by_key["root"].path, "/")

    def test_describe_file_and_folder(self):
        file_rows = dict(self.model.describe(str(self.home / "notes.txt")))
        self.assertEqual(file_rows["Type"], "Text file")
        self.assertEqual(file_rows["Size"], "11 B")
        self.assertIn("Permissions", file_rows)
        folder_rows = dict(self.model.describe(str(self.folder)))
        self.assertEqual(folder_rows["Type"], "Folder")
        self.assertEqual(folder_rows["Items"], "1")

    def test_create_folder_is_unique(self):
        first = self.model.create_folder(str(self.home), "New Folder")
        second = self.model.create_folder(str(self.home), "New Folder")
        self.assertEqual(os.path.basename(first), "New Folder")
        self.assertEqual(os.path.basename(second), "New Folder (copy)")
        self.assertTrue(os.path.isdir(first))
        self.assertTrue(os.path.isdir(second))

    def test_create_file_is_unique_and_writes_text(self):
        first = self.model.create_file(str(self.home), "note.txt", "hello")
        second = self.model.create_file(str(self.home), "note.txt")
        self.assertEqual(os.path.basename(first), "note.txt")
        self.assertEqual(os.path.basename(second), "note (copy).txt")
        self.assertEqual(Path(first).read_text(encoding="utf-8"), "hello")
        self.assertEqual(Path(second).read_text(encoding="utf-8"), "")

    def test_free_space_reports_the_mount(self):
        total, used, free = self.model.free_space(str(self.home))
        self.assertGreater(total, 0)
        self.assertGreaterEqual(used, 0)
        self.assertGreaterEqual(free, 0)
        self.assertLessEqual(used + free, total)

    def test_free_space_missing_path_returns_none(self):
        self.assertIsNone(self.model.free_space(str(self.root / "missing")))

    def test_rename_and_collision(self):
        source = self.home / "notes.txt"
        target = self.model.rename(str(source), "renamed.txt")
        self.assertEqual(os.path.basename(target), "renamed.txt")
        self.assertTrue(os.path.exists(target))
        with self.assertRaises(FileExistsError):
            self.model.rename(str(target), "report.md")
        with self.assertRaises(ValueError):
            self.model.rename(str(target), "bad/name")

    def test_copy_file_and_folder(self):
        target_dir = self.root / "target"
        target_dir.mkdir()
        copied = self.model.copy_paths(
            [str(self.home / "notes.txt"), str(self.folder)], str(target_dir)
        )
        self.assertEqual(len(copied), 2)
        self.assertTrue((target_dir / "notes.txt").is_file())
        self.assertTrue((target_dir / "Project" / "child.txt").is_file())

    def test_copy_collision_gets_copy_suffix(self):
        target_dir = self.root / "target"
        target_dir.mkdir()
        (target_dir / "notes.txt").write_text("old", encoding="utf-8")
        copied = self.model.copy_paths(
            [str(self.home / "notes.txt")], str(target_dir)
        )
        self.assertEqual(os.path.basename(copied[0]), "notes (copy).txt")
        self.assertEqual(
            (target_dir / "notes (copy).txt").read_text(encoding="utf-8"),
            "hello world",
        )

    def test_copy_folder_into_itself_is_refused(self):
        with self.assertRaises(ValueError):
            self.model.copy_paths([str(self.folder)], str(self.folder / "sub"))

    def test_move_file_and_folder(self):
        target_dir = self.root / "target"
        target_dir.mkdir()
        moved = self.model.move_paths(
            [str(self.home / "notes.txt"), str(self.folder)], str(target_dir)
        )
        self.assertEqual(len(moved), 2)
        self.assertFalse((self.home / "notes.txt").exists())
        self.assertTrue((target_dir / "notes.txt").is_file())
        self.assertTrue((target_dir / "Project" / "child.txt").is_file())

    def test_move_in_same_directory_is_noop(self):
        moved = self.model.move_paths(
            [str(self.home / "notes.txt")], str(self.home)
        )
        self.assertEqual(moved, [])
        self.assertTrue((self.home / "notes.txt").exists())

    def test_trash_paths_uses_injected_trash(self):
        trashed = []
        model = FileExplorer(home=str(self.home), trash=trashed.append)
        model.trash_paths([str(self.home / "notes.txt")])
        self.assertEqual(trashed, [str(self.home / "notes.txt")])
        self.assertTrue((self.home / "notes.txt").exists())

    def test_default_trash_moves_file_and_writes_info(self):
        trash_dir = self.root / "Trash"
        model = FileExplorer(home=str(self.home), trash_dir=str(trash_dir))
        model.trash_paths([str(self.home / "notes.txt")])
        self.assertFalse((self.home / "notes.txt").exists())
        self.assertTrue((trash_dir / "files" / "notes.txt").is_file())
        info = (trash_dir / "info" / "notes.txt.trashinfo").read_text(
            encoding="utf-8"
        )
        self.assertIn("[Trash Info]", info)
        self.assertIn("Path=", info)
        self.assertIn("DeletionDate=", info)

    def test_delete_paths_removes_files_and_folders(self):
        self.model.delete_paths([str(self.folder), str(self.home / "notes.txt")])
        self.assertFalse(self.folder.exists())
        self.assertFalse((self.home / "notes.txt").exists())
        self.assertTrue((self.home / "report.md").exists())

    def test_preview_text_and_binary(self):
        self.assertEqual(
            self.model.preview_text(str(self.home / "notes.txt")), "hello world"
        )
        self.assertIsNone(self.model.preview_text(str(self.home / "binary.bin")))

    def test_preview_text_truncates(self):
        self.assertEqual(
            self.model.preview_text(str(self.home / "notes.txt"), 5),
            "hello\n…",
        )

    def test_symlink_entry(self):
        link = self.home / "notes-link"
        try:
            os.symlink(self.home / "notes.txt", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        entries = {
            item.name: item for item in self.model.list_entries(str(self.home))
        }
        self.assertTrue(entries["notes-link"].is_symlink)
        self.assertEqual(entries["notes-link"].kind, "Symbolic link")


class FileExplorerModuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(self._tmp.name)
        self.module = FileExplorerModule(ModuleContext(self.storage))

    def test_commands_are_registered_and_unique(self):
        commands = self.module.commands()
        ids = [command.id for command in commands]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("files.search", ids)
        self.assertIn("files.new_file", ids)
        self.assertIn("files.terminal", ids)
        for command in commands:
            self.assertTrue(command.id.startswith("files."))
            self.assertEqual(command.scope, "files")


if __name__ == "__main__":
    unittest.main(verbosity=2)
