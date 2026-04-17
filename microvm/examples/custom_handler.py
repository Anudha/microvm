"""Intercept requests with a custom handler.

The handler signature is (method, url, headers, body) -> (status, headers, body) | None.
Returning None passes through to upstream.
"""
from microvm import Instance


def my_handler(method, url, headers, body):
    # Rewrite example.com requests to serve a local fake page.
    if "example.com" in url:
        return (200, {"Content-Type": "text/html"},
                b"<h1>served by microvm handler</h1>")
    return None  # pass through


def main() -> None:
    inst = Instance(name="handler-demo", start_url="http://example.com/")
    inst.net.set_handler(my_handler)
    inst.start()
    input("press enter to stop... ")
    inst.stop()
    for e in inst.net.journal():
        print(e)


if __name__ == "__main__":
    main()
