"""Boomerang CLI (provisional; owner C). Role A's commands live in cli_platform.py."""
from __future__ import annotations

import typer

from . import cli_platform

app = typer.Typer(help="Boomerang: AWS <-> Leaseweb migration", no_args_is_help=True)
cli_platform.register(app)
cli_platform.register_extra(app)

if __name__ == "__main__":
    app()
