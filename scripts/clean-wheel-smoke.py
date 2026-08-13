from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: clean-wheel-smoke.py DIST_WHEEL")
    wheel = Path(sys.argv[1]).resolve()
    if not wheel.is_file():
        raise SystemExit(f"wheel not found: {wheel}")
    with tempfile.TemporaryDirectory(prefix="paper-trading-wheel-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        subprocess.run(  # noqa: S603 - fixed local interpreter and generated path
            [sys.executable, "-m", "venv", str(environment)], check=True
        )
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(  # noqa: S603 - fixed interpreter installs selected local wheel
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
            check=True,
            cwd=root,
        )
        probe = """
from pathlib import Path
from tempfile import TemporaryDirectory
from backend.app.main import create_app
with TemporaryDirectory() as directory:
    database = Path(directory) / 'installed.sqlite3'
    app = create_app(database_path=database, start_worker=False)
    assert app.title == 'Paper Trading Only'
    with app.state.database.connect() as connection:
        versions = connection.execute(
            'SELECT version FROM schema_migrations ORDER BY version'
        ).fetchall()
    assert [row[0] for row in versions] == list(range(1, 9))
print('installed wheel source-free create_app/migrations smoke: PASS')
"""
        subprocess.run(  # noqa: S603 - fixed interpreter executes a constant probe
            [str(python), "-I", "-c", probe], check=True, cwd=root
        )


if __name__ == "__main__":
    main()
