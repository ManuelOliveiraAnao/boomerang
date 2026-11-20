"""Role A CLI commands: lab, aws, verify, data."""
from __future__ import annotations

import typer
from rich.console import Console

from . import lab

console = Console()
lab_app = typer.Typer(help="Fake AWS (moto) and adapter test services", no_args_is_help=True)
aws_app = typer.Typer(help="Provision / test apps on the fake AWS", no_args_is_help=True)


def register(app: typer.Typer) -> None:
    app.add_typer(lab_app, name="lab")
    app.add_typer(aws_app, name="aws")


def _fail(msg: str) -> None:
    console.print(f"[red]FAIL {msg}[/red]")
    raise typer.Exit(1)


@lab_app.command("up")
def lab_up(adapters: bool = typer.Option(False, help="Also start test Postgres + RabbitMQ (Docker)"),
           backend: str = typer.Option("auto", help="auto | docker | process")):
    try:
        used = lab.up(adapters=adapters, backend=backend)
    except lab.LabError as e:
        _fail(str(e))
    console.print(f"[green]OK fake AWS on {lab.AWS_ENDPOINT}[/green] (backend: {used})")


@lab_app.command("down")
def lab_down():
    lab.down()
    console.print("[green]OK lab stopped[/green]")


@lab_app.command("status")
def lab_status():
    for name, ok in lab.status().items():
        console.print(f"{'[green]OK' if ok else '[red]FAIL'} {name}[/]")


@lab_app.command("reset")
def lab_reset():
    try:
        lab.reset_moto()
    except OSError as e:
        _fail(f"moto not reachable: {e}")
    console.print("[green]OK fake AWS state wiped[/green]")


DEFAULT_PAM = "tests/fixtures/pam_shoplite.json"


def _load_pam(path: str):
    from pathlib import Path

    from .models import AppModel
    return AppModel.model_validate_json(Path(path).read_text(encoding="utf-8"))


@aws_app.command("provision")
def aws_provision(pam: str = typer.Option(DEFAULT_PAM, help="PAM json of the app"),
                  seed: int = typer.Option(0, help="ShopLite demo orders to create (0 = none)"),
                  reset: bool = typer.Option(False, help="Wipe the fake AWS first")):
    from . import provisioner_aws
    lab.up()
    if reset:
        lab.reset_moto()
    env = provisioner_aws.provision(_load_pam(pam))
    if seed:
        console.print(f"seeded: {provisioner_aws.seed_shoplite(env, n_orders=seed)}")
    for k, v in env.items():
        console.print(f"{k}={v}")


@aws_app.command("test")
def aws_test(app_dir: str = typer.Argument("sample-app/aws"),
             tests: str = typer.Option("sample-app/tests", help="Original tests directory"),
             pam: str = typer.Option(DEFAULT_PAM)):
    from pathlib import Path

    from . import provisioner_aws, verifier
    lab.up()
    env = provisioner_aws.provision(_load_pam(pam))
    report = verifier.run_pytest(Path(app_dir), Path(tests), env, "aws")
    color = "green" if report.ok else "red"
    console.print(f"[{color}]{report.passed}/{report.total} passed[/{color}] in {report.duration_s}s")
    for f in report.failures:
        console.print(f"[red]FAIL {f.test}[/red]: {f.message}")
    if not report.ok:
        raise typer.Exit(1)


def register_extra(app: typer.Typer) -> None:
    app.command("verify")(verify_cmd)
    app.command("data")(data_cmd)


def verify_cmd(leaseweb_dir: str = typer.Argument(..., help="runs/<id>/leaseweb"),
               attempt: int = typer.Option(1)):
    """Config valid -> boots -> schema -> original tests -> health."""
    from pathlib import Path

    from . import verifier
    v = verifier.verify(Path(leaseweb_dir), attempt)
    for c in v.checks:
        console.print(f"{'[green]OK' if c.ok else '[red]FAIL'}[/] {c.name} ({c.duration_s}s) {c.detail[:300]}")
    if not v.ok:
        raise typer.Exit(1)


def data_cmd(leaseweb_dir: str = typer.Argument(..., help="runs/<id>/leaseweb"),
             direction: str = typer.Option("aws->leaseweb", help="aws->leaseweb | leaseweb->aws"),
             pam: str = typer.Option(DEFAULT_PAM)):
    """Copy data between the fake AWS and the booted Leaseweb stack; print counts."""
    from pathlib import Path

    from . import datamover, provisioner_aws, verifier
    model = _load_pam(pam)
    aws_env = provisioner_aws.app_env(model, provisioner_aws.resource_names(model))
    result = datamover.move(model, direction, aws_env, verifier.laptop_env(Path(leaseweb_dir)))
    for c in result.counts:
        console.print(f"{'[green]OK' if c.source_count == c.target_count and c.checksum_match else '[red]FAIL'}[/] "
                      f"{c.component_id}: {c.source_count} -> {c.target_count}")
    if not result.ok:
        raise typer.Exit(1)
