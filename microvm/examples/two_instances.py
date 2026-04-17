"""Run two isolated instances side-by-side with different binaries.

Edit the two binary paths below for your system and run:
    python examples/two_instances.py
"""
from microvm import Registry

# Point these at two different installed browsers. They can also be two
# versions of the same browser.
BROWSER_A = "/Applications/Chromium.app/Contents/MacOS/Chromium"
BROWSER_B = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def main() -> None:
    reg = Registry()
    a = reg.create(name="alice", binary=BROWSER_A,
                   start_url="https://example.com")
    b = reg.create(name="bob",   binary=BROWSER_B,
                   start_url="https://example.org",
                   blocklist={"ads.example.com"})

    a.start(); b.start()
    print("alice:", a.info())
    print("bob:  ", b.info())

    input("press enter to stop both... ")
    reg.stop_all()

    # Dump per-instance journals.
    a.fs.dump_journal(); a.net.dump_journal(a.root / "net_journal.jsonl")
    b.fs.dump_journal(); b.net.dump_journal(b.root / "net_journal.jsonl")


if __name__ == "__main__":
    main()
