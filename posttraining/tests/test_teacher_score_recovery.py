"""An interrupted handoff from a sealed teacher worker to its CPU scorer can be completed offline."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import flockcue_session_cpu.__main__ as session_process
from flockcue_session_cpu.teacher_recovery import recover_scores
from run_teacher_service import advance, prepare
from test_teacher_service import provider


def test_only_never_started_scorer_recovers_without_contacting_the_teacher(tmp_path, monkeypatch):
    batch = tmp_path / "batch"
    sid = "04cd6269"
    scene = batch / "sessions" / sid
    launch = session_process.launch

    def interrupt(role, directory, env, previous):
        if role == "scorer":
            raise InterruptedError("Injected coordinator loss before scorer launch")
        return launch(role, directory, env, previous)

    with provider("disconnect") as (url, received):
        prepare(batch, "loopback_http", "CNY", url)
        with monkeypatch.context() as patch:
            patch.setattr(session_process, "launch", interrupt)
            with pytest.raises(InterruptedError):
                advance(batch, sid)
        assert len(received) == 1 and (scene/"worker-exit.json").is_file() and not (scene/"scorer.log").exists()
        journal = batch/"cost-ledger.jsonl"
        sealed_journal = journal.read_bytes()
        # launch() creates this claim before spawning any scorer process, including an orphan.
        (scene/"scorer.log").touch()
        assert not recover_scores(scene, batch)
        (scene/"scorer.log").unlink()
        assert recover_scores(scene, batch)
        assert not recover_scores(scene, batch)
        status = advance(batch, sid)
        worker = json.loads((scene/"worker-exit.json").read_bytes())
        scorer = json.loads((scene/"scorer-exit.json").read_bytes())
        audit = json.loads((scene/"scorer-boundary.json").read_bytes())
        assert scorer["returncode"] == 0 and worker["pid"] != scorer["pid"]
        assert audit["network_attempts"] == [] and not audit["agent_imported"]
        assert journal.read_bytes() == sealed_journal and len(received) == 1
        assert status["blocked"] and status["reservations"] == 1
        scores = json.loads((scene/"session_scores.json").read_bytes())
        assert len(scores["tasks"]) == 1 and (scene/"tasks/01-a05a871a/initial-score.json").exists()
        report = {"worker_pid":worker["pid"], "scorer_pid":scorer["pid"], "network_attempts":audit["network_attempts"],
                  "peer_requests_before":1, "peer_requests_after":len(received), "ledger_byte_identical":True,
                  "original_scorer_never_started":True, "scorer_claim_excluded":True,
                  "repeated_recovery_skipped":True, "scored_tasks":scores["tasks"], "planned_tasks":len(status["tasks"])}
        (tmp_path/"score-handoff.json").write_text(json.dumps(report, indent=2)+"\n")
