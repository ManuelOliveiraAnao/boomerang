"""Doctor UI (owner D, docs/18 §6): the demo incident follows the contract; the runner sends the guide's commands."""
import json
from pathlib import Path

from boomerang.models import Incident
from ui import runner

ROOT = Path(__file__).resolve().parent.parent


def test_demo_incident_matches_the_contract():
    inc = Incident.model_validate(json.loads((ROOT / "fixtures" / "doctor-demo.json").read_text(encoding="utf-8")))
    d = inc.diagnosis
    assert d.rule == "db_down" and d.component_id == "orders" and inc.severity == "down"
    assert [a.id for a in d.actions] == ["restart_service", "move_back", "reverify"]   # ranked, best first
    assert any(not s.ok and s.source == "probe" for s in d.evidence)


def test_runner_sends_the_doctor_commands_of_the_guide():
    assert runner._args("doctor", "r1", "app") == ["doctor", "r1"]
    assert runner._args("doctor-fix", "r1", "app", args=["move_back", "orders"]) == \
        ["doctor", "fix", "r1", "move_back", "orders", "--by", "ui"]
    assert runner._args("doctor-break", "r1", "app", args=["postgres"]) == ["doctor", "break", "r1", "postgres"]
