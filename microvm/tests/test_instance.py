"""Instance-level tests that don't require an actual browser binary.

We point `binary` at a no-op executable (`/bin/true` on POSIX) just to verify
that the harness wires things up correctly. On CI this is fine; on Windows
these tests are skipped.
"""
import os
import sys
import pytest

from microvm import Instance, Registry


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="no trivial no-op exe on Windows CI; covered in manual testing",
)


def test_instance_builds_env_and_argv(tmp_path):
    inst = Instance(
        name="t1",
        binary="/bin/true",
        start_url="about:blank",
        root=tmp_path,
    )
    assert inst.fs.root == tmp_path / "instances" / "t1"
    argv = inst._build_argv()
    assert argv[0] == "/bin/true"
    assert any(a.startswith("--user-data-dir=") for a in argv)
    assert any(a.startswith("--proxy-server=http://127.0.0.1:") for a in argv) \
        or any(a.startswith("--proxy-server=http://127.0.0.1:0") for a in argv)
    env = inst._build_env()
    assert env["MICROVM_INSTANCE"] == "t1"


def test_two_instances_have_disjoint_roots_and_ports(tmp_path):
    a = Instance(name="a", binary="/bin/true", root=tmp_path)
    b = Instance(name="b", binary="/bin/true", root=tmp_path)
    a.net.start(); b.net.start()
    try:
        assert a.root != b.root
        assert a.net.port != b.net.port
    finally:
        a.net.stop(); b.net.stop()


def test_registry_rejects_duplicate_name(tmp_path):
    reg = Registry()
    reg.create(name="same", binary="/bin/true", root=tmp_path)
    with pytest.raises(ValueError):
        reg.create(name="same", binary="/bin/true", root=tmp_path)


def test_start_and_stop_noop_binary(tmp_path):
    inst = Instance(name="noop", binary="/bin/true",
                    start_url="about:blank", root=tmp_path)
    inst.start()
    # /bin/true exits immediately. Give it a moment then stop cleanly.
    import time; time.sleep(0.2)
    inst.stop()
    assert not inst.running
