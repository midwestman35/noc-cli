def test_watch_package_importable():
    from noc_cli.watch import poller  # noqa: F401 — the import is the assertion
