# microvm

A tiny "mini-OS" for running **multiple Chromium-family browsers in fully isolated instances** on Windows and macOS. Pure Python, stdlib only, zero runtime dependencies.

Each instance gets its own profile, its own network proxy, its own scoped filesystem, its own process, and its own environment. They can run concurrently and cannot see each other's state.

```python
from microvm import Registry

reg = Registry()
alice = reg.create(name="alice", binary="/Applications/Chromium.app/Contents/MacOS/Chromium",
                   start_url="https://example.com")
bob   = reg.create(name="bob",   binary="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   start_url="https://example.org", blocklist={"ads.example.com"})

alice.start(); bob.start()
# ... two fully isolated browsers running side-by-side ...
reg.stop_all()
```

---

## What this is (and isn't)

**This is** a *process-wrapper* mini-OS. It launches each browser as a normal host process but first constructs the things a mini-OS would normally give a program: a scoped filesystem, a private network interface, a scrubbed environment, and a registry that tracks who is running.

**This is not** a virtual machine, a container, or a security sandbox. The browser still executes on the host kernel. If you need the browser itself to be unable to touch the host filesystem or bypass the proxy, that is an *OS-level sandboxing* problem (AppContainer on Windows, `sandbox-exec` or the Seatbelt profile on macOS) — a different project, and one that cannot be solved in portable pure Python.

The contract microvm offers is precise: **instances are isolated from each other, not from the host.** Within that contract, the isolation is strong and the implementation is small.

---

## Architecture

### The mental model: "mini-OS" = three things

A real OS gives each process (1) a view of the filesystem, (2) a view of the network, and (3) an environment / process identity. microvm gives each *instance* its own object for each of those:

```
                 ┌────────────────────────────┐
                 │         Instance           │
                 │   (name, id, pid, spec)    │
                 └──┬───────┬──────────┬──────┘
                    │       │          │
           ┌────────▼──┐ ┌──▼──────┐ ┌─▼──────────────┐
           │ Filesystem│ │ Network │ │ scrubbed env + │
           │ Interface │ │Interface│ │  argv builder  │
           └────┬──────┘ └────┬────┘ └────────┬───────┘
                │             │               │
         ┌──────▼─────┐ ┌─────▼──────┐ ┌──────▼──────┐
         │ scoped     │ │ loopback   │ │ subprocess  │
         │ root dir   │ │ HTTP proxy │ │  .Popen     │
         │ + journal  │ │ + journal  │ │ (the browser)│
         └────────────┘ └────────────┘ └─────────────┘
```

Every field above is *per-instance*. Two instances share no state — no dict, no port, no file, no env var.

### Why a process wrapper and not a real VM

I considered four designs:

| approach | isolates instances? | cross-platform? | complexity | runtime deps |
|---|---|---|---|---|
| **process wrapper** (chosen) | yes | yes | ~1k LOC | none |
| Linux-style containers | yes | no (macOS has no namespaces) | high | kernel features |
| full VM (QEMU / Virtualization.framework / Hyper-V) | yes + host isolation | yes, but platform-forked | very high | multi-GB images |
| in-Python simulated kernel | philosophically yes | yes | high | would have to re-implement Chromium's syscall deps |

The process wrapper is the **only** option that actually runs real Chromium, works on both Windows and macOS with the same code, and keeps the codebase small enough to audit in one sitting. The other three trade away cross-platform simplicity for a kind of isolation (host-sandboxing) that wasn't in the requirements.

### Why the two interfaces look the way they do

The two interfaces — `FilesystemInterface` and `NetworkInterface` — have a deliberately parallel shape:

- Both have a **journal** (an append-only list of events) that records every operation.
- Both have a **dump_journal()** that writes JSONL to disk for after-the-fact analysis.
- Both are **owned by the instance** and constructed per-instance, never shared.
- Both have a small **extension seam** — `set_handler()` for network, subclassing for FS — so callers can intercept without forking the library.

This parallel is on purpose. It's the mini-OS's "syscall surface": the sanctioned way for the harness to mediate between the outside world and an instance. Keeping the two interfaces shaped alike makes the library easy to reason about — if you've understood one, you've understood the other.

### Filesystem isolation

Each instance has a root directory laid out as:

```
<state_root>/instances/<name>/
  profile/     <- Chromium's --user-data-dir
  downloads/   <- download target (seeded via Preferences)
  work/        <- general scratch for the harness / user scripts
```

`state_root()` is platform-aware: `%LOCALAPPDATA%\microvm` on Windows, `~/Library/Application Support/microvm` on macOS, overridable with `$MICROVM_HOME`.

`FilesystemInterface.resolve()` refuses absolute paths and refuses anything that resolves outside the root. This is confinement for *harness* code — we do not and cannot prevent the browser itself from reading host files. But every path the harness hands to user code, or uses to route downloads, lives under the instance root.

### Network isolation

Each instance launches its **own** `ThreadingHTTPServer` on `127.0.0.1` on an ephemeral port, and passes `--proxy-server=http://127.0.0.1:<port>` to the browser. So:

- Two instances → two proxies → two independent logs. No shared port, no shared list of requests, no shared blocklist.
- The proxy handles plain HTTP (parse, forward, log) and HTTPS via blind `CONNECT` tunneling (relay bytes, log target + byte counts).
- An optional `handler(method, url, headers, body) -> (status, headers, body) | None` lets callers short-circuit requests — useful for mocking, for blocking, for fault injection.

**We deliberately do not MITM HTTPS.** TLS interception requires installing a trusted local CA, which is security-sensitive and orthogonal to "isolate instances from each other." If you want full request-body visibility, combine microvm with mitmproxy or similar and point the instance's proxy at it.

### Process isolation

- Each instance is a separate `subprocess.Popen` with its own PID and its own scrubbed environment.
- On Windows we pass `DETACHED_PROCESS` so the browser doesn't inherit the harness's console handles.
- On POSIX we use `start_new_session=True` so Ctrl-C on the harness doesn't cascade into the browser.
- The environment is built from a short allow-list (`SYSTEMROOT`, `PATH`, etc.) plus explicit `env_passthrough=[...]` and `extra_env={...}` parameters. No ambient host env leaks by default.
- Three vars are injected: `MICROVM_INSTANCE`, `MICROVM_INSTANCE_ID`, `MICROVM_ROOT` — so child code can detect it's running inside microvm.

### Concurrency

`Registry` is a `{name: Instance}` dict behind a lock. It refuses duplicate names (so `alice` means one specific thing). Everything else — ports, dirs, journals — is already per-instance by construction, so concurrent `start()` calls Just Work.

---

## Installation

```bash
git clone <your-fork>
cd microvm
pip install -e .
# or, for tests:
pip install -e ".[dev]"
pytest
```

Python 3.10+. No runtime deps.

## CLI

```bash
# Launch and wait until the browser exits
microvm launch --name alice --url https://example.com --wait

# Launch with an explicit binary and extra flags
microvm launch --name bob \
    --binary "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --url https://example.org \
    --block ads.example.com tracker.example.com \
    --extra --disable-gpu --wait

# Inspect / clean up
microvm list
microvm logs alice
microvm wipe alice
```

## Library

```python
from microvm import Instance

with Instance(name="demo", start_url="https://example.com") as inst:
    print(inst.info())
    # ... browser is running, proxy is up, FS root exists ...
    for event in inst.net.journal():
        print(event)
# stop() is called automatically on exit; net proxy is shut down.
```

### Custom request handler

```python
def handler(method, url, headers, body):
    if "tracking" in url:
        return (204, {}, b"")          # swallow
    if url.endswith("/api/config"):
        return (200, {"Content-Type": "application/json"}, b'{"mocked": true}')
    return None                         # pass through

inst.net.set_handler(handler)
```

### Custom filesystem root

```python
from pathlib import Path
Instance(name="scratch", root=Path("/tmp/my-microvms"), binary="...").start()
```

---

## Layout

```
microvm/
├── microvm/
│   ├── __init__.py
│   ├── instance.py           # Instance class — the "process + interfaces" bundle
│   ├── registry.py           # concurrent-instance tracker
│   ├── platform_utils.py     # browser lookup, state_root per OS
│   ├── interfaces/
│   │   ├── __init__.py
│   │   ├── filesystem.py     # FilesystemInterface + FSEvent
│   │   └── network.py        # NetworkInterface + NetEvent + proxy
│   └── cli/
│       └── __main__.py       # `microvm` command
├── examples/
│   ├── two_instances.py
│   └── custom_handler.py
├── tests/
│   ├── test_filesystem.py
│   ├── test_network.py
│   └── test_instance.py
├── pyproject.toml
├── LICENSE
└── README.md
```

Roughly 1,000 lines of Python including comments and tests. The whole thing is designed to be readable in one sitting; if something looks more complicated than it needs to be, it's a bug, not a feature.

---

## Non-goals

- **Host sandboxing.** The browser can still read your home directory. If you need that guarantee, wrap microvm in AppContainer / `sandbox-exec` / a real VM.
- **TLS interception.** The proxy tunnels HTTPS blindly. Pair with mitmproxy if you need request-body visibility.
- **Linux support.** The project brief targets Windows and macOS. Nothing in the code is Windows/macOS-specific *except* the default browser-binary lookup, so Linux likely works if you pass `binary=` explicitly — but it's not tested here.
- **Profile migration / cloud sync / headless automation DSL.** Out of scope. Use Playwright or Puppeteer for scripted browsing; microvm is about isolation, not automation.

## License

MIT. See `LICENSE`.
