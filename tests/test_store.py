from noc_cli import store


def test_connect_creates_dirs_sets_pragmas_and_row_factory(tmp_path):
    db = tmp_path / "nested" / "noc.db"
    conn = store.connect(db)
    try:
        assert db.exists()
        assert conn.execute("PRAGMA foreign_keys;").fetchone()[0] == 1
        conn.execute("CREATE TABLE t (a TEXT);")
        conn.execute("INSERT INTO t (a) VALUES ('x');")
        row = conn.execute("SELECT a FROM t;").fetchone()
        assert row["a"] == "x"  # sqlite3.Row enables name access
    finally:
        conn.close()
