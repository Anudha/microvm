import tempfile
from pathlib import Path

import pytest

from microvm.interfaces.filesystem import FilesystemInterface, FilesystemEscape


def test_layout_created(tmp_path):
    fs = FilesystemInterface(tmp_path / "a")
    assert fs.profile_dir.is_dir()
    assert fs.downloads_dir.is_dir()
    assert fs.work_dir.is_dir()


def test_read_write_roundtrip(tmp_path):
    fs = FilesystemInterface(tmp_path / "a")
    fs.write_bytes("work/hello.txt", b"hi")
    assert fs.read_bytes("work/hello.txt") == b"hi"


def test_escape_rejected(tmp_path):
    fs = FilesystemInterface(tmp_path / "a")
    with pytest.raises(FilesystemEscape):
        fs.resolve("../outside")
    with pytest.raises(FilesystemEscape):
        fs.resolve("/etc/passwd")


def test_journal_records_ops(tmp_path):
    fs = FilesystemInterface(tmp_path / "a")
    fs.write_bytes("work/a", b"x")
    fs.read_bytes("work/a")
    fs.delete("work/a")
    ops = [e.op for e in fs.journal()]
    assert ops == ["write", "read", "delete"]


def test_two_instances_are_disjoint(tmp_path):
    a = FilesystemInterface(tmp_path / "a")
    b = FilesystemInterface(tmp_path / "b")
    a.write_bytes("work/secret", b"A")
    b.write_bytes("work/secret", b"B")
    assert a.read_bytes("work/secret") == b"A"
    assert b.read_bytes("work/secret") == b"B"
    # Journals are independent.
    assert len(a.journal()) == 2
    assert len(b.journal()) == 2
