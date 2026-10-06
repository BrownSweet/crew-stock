"""Bake the public URL prefix into the plain HTML/JavaScript frontend."""

import argparse
import re
import shutil
from pathlib import Path


def build(source: Path, target: Path, base: str) -> str:
    if not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*/?", base):
        raise ValueError("VITE_BASE_PATH must be / or a path such as /crew/")
    base = base.rstrip("/") + "/"
    if source.resolve() == target.resolve():
        raise ValueError("Build output must be separate from the source directory")
    shutil.copytree(source, target, dirs_exist_ok=True)
    index = source.joinpath("index.html").read_text()
    index = index.replace(
        'name="crew-base-path" content="/"', f'name="crew-base-path" content="{base}"'
    )
    index = index.replace('"/static/', f'"{base}static/')
    index = index.replace('href="/"', f'href="{base}"')
    target.joinpath("index.html").write_text(index)
    target.joinpath(".base-path").write_text(base)
    return base


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="/")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    build(args.source, args.target, args.base)
