"""Offline gates for the single-arm remedies pilot; no model/service execution."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

# These tests also run in detached, materialized legacy workspaces.
REPOSITORY = next(p for p in Path(__file__).resolve().parents if (p / "scripts").is_dir())
sys.path.insert(0, str((REPOSITORY / "scripts")))
import b1_remedies_pilot as pilot


@pytest.fixture
def study_inputs(tmp_path, monkeypatch):
    spec = tmp_path / "spec"
    spec.mkdir()
    for name in ("baseline.json", "baseline-audit.json"):
        (spec / name).write_text(json.dumps({"fixture": name}))
    monkeypatch.setattr(pilot, "SPEC", spec)
    return spec


def test_plan_keeps_same_three_scenes_original_task_order_and_60_320(study_inputs):
    plan = pilot.build_plan()
    old = pilot.r.read_json(pilot.OLD_PILOT)["plans"]["pilot"]
    assert plan["sessions"] == old["sessions"]
    assert [scene["id"] for scene in plan["sessions"]] == ["ad34350a", "ef36e27a", "3973d0d6"]
    assert all(len(scene["tasks"]) == 20 for scene in plan["sessions"])
    assert (plan["expected_tasks"], plan["expected_checks"]) == (60, 320)
    assert plan["protocol"]["timeout_seconds"] == 500
    assert plan["study"]["workers"] == 2
    assert plan["study"]["agent_commit"] == pilot.METHOD_COMMIT
    assert "no new B0 execution" in plan["study"]["baseline"]
    assert plan["study"]["baseline_inputs"] == {
        name: pilot.r.digest((study_inputs / name).read_bytes())
        for name in ("baseline.json", "baseline-audit.json")
    }


def test_changed_scene_order_is_rejected(study_inputs, monkeypatch):
    monkeypatch.setattr(pilot, "SCENES", tuple(reversed(pilot.SCENES)))
    with pytest.raises(ValueError, match="previously fixed 60"):
        pilot.build_plan()


def test_changed_task_order_in_previous_selection_is_rejected(study_inputs, monkeypatch, tmp_path):
    old = pilot.r.read_json(pilot.OLD_PILOT)
    old["plans"]["pilot"]["sessions"][0]["tasks"].reverse()
    path = tmp_path / "changed-selection.json"
    path.write_text(json.dumps(old))
    monkeypatch.setattr(pilot, "OLD_PILOT", path)
    with pytest.raises(ValueError, match="previously fixed 60"):
        pilot.build_plan()


def test_baseline_change_after_plan_freeze_is_rejected(study_inputs):
    (study_inputs / "plan.json").write_text(json.dumps(pilot.build_plan()))
    assert pilot.load_plan()["expected_tasks"] == 60
    (study_inputs / "baseline.json").write_text('{"changed": true}')
    with pytest.raises(ValueError, match="baseline changed"):
        pilot.load_plan()


def test_real_historical_baseline_is_ready_without_model_execution():
    pilot.assert_baseline(pilot.build_plan())


@pytest.fixture
def local_settings(tmp_path, monkeypatch):
    settings = {"selected_provider": "bailian_qwen_flash", "provider_configs": {
        "bailian_qwen_flash": {"api_key": "test-qwen-config-key", "default_model": "qwen3.8-flash"},
        "deepseek_official": {"type": "openai-compatible", "base_url": "https://api.deepseek.com/",
                              "default_model": "deepseek-flash", "api_key": ""},
    }}
    path = tmp_path / "agent4drone/llm_settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps(settings))
    monkeypatch.setattr(pilot, "ROOT", tmp_path)
    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY", "DASHSCOPE_API_KEY", "REPRO_GATEWAY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    return path, settings


def test_explicit_official_provider_ignores_selected_qwen_and_generic_keys(local_settings, monkeypatch):
    path, _ = local_settings
    before = path.read_bytes()
    for name in ("OPENAI_API_KEY", "LLM_API_KEY", "DASHSCOPE_API_KEY", "REPRO_GATEWAY_API_KEY"):
        monkeypatch.setenv(name, "test-wrong-provider-key")
    settings, key = pilot.settings_for_run()
    assert key is None
    assert settings["selected_provider"] == "deepseek_official"
    assert set(settings["provider_configs"]) == {"deepseek_official"}
    assert settings["provider_configs"]["deepseek_official"]["api_key"] == ""
    assert path.read_bytes() == before
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    settings, key = pilot.settings_for_run()
    assert key == "test-deepseek-key"
    assert "test-deepseek-key" not in json.dumps(settings)
    assert path.read_bytes() == before


def test_explicit_provider_configuration_key_stays_out_of_archived_settings(local_settings, monkeypatch):
    path, original = local_settings
    original["provider_configs"]["deepseek_official"]["api_key"] = "test-config-deepseek-key"
    path.write_text(json.dumps(original))
    before = path.read_bytes()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-env-deepseek-key")
    settings, key = pilot.settings_for_run()
    assert key == "test-config-deepseek-key"
    assert "test-config-deepseek-key" not in json.dumps(settings)
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("type", "other"), ("base_url", "https://unrelated.invalid"), ("default_model", "qwen3.8-flash"),
])
def test_endpoint_model_and_provider_type_must_match_history(local_settings, field, value):
    path, settings = local_settings
    settings["provider_configs"]["deepseek_official"][field] = value
    path.write_text(json.dumps(settings))
    with pytest.raises(ValueError, match="official deepseek-flash"):
        pilot.settings_for_run()


@pytest.fixture
def frozen_agent_hashes(monkeypatch):
    contents = {"agent4drone/uav_agent.py": b"agent bytes\n",
                "agent4drone/relative_movement.py": b"relative action bytes\n"}

    def git_output(args, *, cwd, text=False):
        assert cwd == pilot.ROOT
        if args[:2] == ["git", "ls-tree"]:
            assert args[4] == pilot.METHOD_COMMIT
            return "\n".join([*contents, "agent4drone/README.md"]) + "\n"
        assert args[:2] == ["git", "show"]
        commit, path = args[2].split(":", 1)
        assert commit == pilot.METHOD_COMMIT
        return contents[path]

    expected = {path: pilot.r.digest(data) for path, data in contents.items()}
    actual = {**expected, "server/main.py": "unrelated-server-hash", "agent4drone/requirements.txt": "requirements"}
    monkeypatch.setattr(pilot.subprocess, "check_output", git_output)
    monkeypatch.setattr(pilot.r, "current_source_hashes", lambda: actual)
    return expected, actual


def test_agent_hashes_use_pinned_commit_and_only_agent_python(frozen_agent_hashes):
    expected, _ = frozen_agent_hashes
    assert pilot.assert_agent_source() == expected


@pytest.mark.parametrize("change", ["modified", "added", "deleted"])
def test_agent_python_change_is_rejected(frozen_agent_hashes, change):
    _, actual = frozen_agent_hashes
    if change == "modified":
        actual["agent4drone/uav_agent.py"] = "different"
    elif change == "added":
        actual["agent4drone/extra.py"] = "extra"
    else:
        del actual["agent4drone/relative_movement.py"]
    with pytest.raises(ValueError, match="differs from the frozen remedies commit"):
        pilot.assert_agent_source()


def test_runtime_uses_archived_python_for_agent_server_and_original_checker(tmp_path, monkeypatch):
    live = tmp_path / "live"
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    original_runner_source = Path(pilot.r.__file__).read_bytes()
    files = {
        "scripts/reproduction.py": original_runner_source,
        "agent4drone/agent_probe.py": b"VALUE = 'frozen agent'\n",
        "server/server_probe.py": b"VALUE = 'frozen server'\n",
        "controller/check_ui/__init__.py": b"",
        "controller/check_ui/agent_checker.py": (
            b"class AgentCheckerApp:\n"
            b"    def evaluate_execution_check_node(self, tree, session_id):\n"
            b"        return {'value': 'frozen checker', 'source': __file__}\n"
        ),
    }
    for name, data in files.items():
        target = live / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for component in ("server", "controller", "agent4drone"):
        python = live / component / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
    asset = live / "server/config/example/example.json"
    asset.parent.mkdir(parents=True)
    asset.write_text('{"fixture": "frozen asset"}')
    data = tmp_path / "data"
    (data / "scenes").mkdir(parents=True)
    scene = data / "scenes/fixture.json"
    scene.write_text('{"id": "fixture"}')

    def snapshot(destination):
        with zipfile.ZipFile(destination / "source.zip", "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        pilot.r.save_json(destination / "source_hashes.json", {name: pilot.r.digest(content) for name, content in files.items()})

    monkeypatch.setattr(pilot, "ROOT", live)
    monkeypatch.setattr(pilot, "DATA", data)
    monkeypatch.setattr(pilot, "load_plan", lambda: {"sessions": [{"file": "scenes/fixture.json"}]})
    monkeypatch.setattr(pilot.r, "code_snapshot", snapshot)
    runtime = pilot.freeze_runtime(attempt)
    for name, content in files.items():
        assert (runtime / name).read_bytes() == content
    assert (runtime / "data/scenes/fixture.json").read_bytes() == scene.read_bytes()
    assert pilot.r.read_json(attempt / "runtime-assets.json") == {
        "server/config/example/example.json": pilot.r.digest(asset.read_bytes())
    }
    for component in ("server", "controller", "agent4drone"):
        assert (runtime / component / ".venv").is_symlink()
        assert (runtime / component / ".venv").resolve() == (live / component / ".venv").resolve()
    (live / "agent4drone/agent_probe.py").write_text("VALUE = 'changed live agent'\n")
    (live / "server/server_probe.py").write_text("VALUE = 'changed live server'\n")
    (live / "controller/check_ui/agent_checker.py").write_text("raise RuntimeError('live checker must not import')\n")
    asset.write_text('{"changed": true}')
    # Only an isolated Python import probe runs; no app, simulator or LLM is started.
    probe = "\n".join([
        "import json, sys",
        f"sys.path.insert(0, {str(runtime / 'scripts')!r})",
        "import reproduction as r",
        "sys.path.insert(0, str(r.ROOT / 'agent4drone'))",
        "sys.path.insert(0, str(r.ROOT / 'server'))",
        "import agent_probe, server_probe",
        "print(json.dumps({'root': str(r.ROOT), 'agent': agent_probe.VALUE, 'server': server_probe.VALUE,",
        "                  'checker': r.upstream_evaluate(None, {}, 'fixture')}))",
    ])
    completed = subprocess.run([str(runtime / "controller/.venv/bin/python"), "-B", "-c", probe],
                               cwd=live, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, check=True, timeout=15)
    imported = json.loads(completed.stdout)
    assert imported["root"] == str(runtime)
    assert imported["agent"] == "frozen agent" and imported["server"] == "frozen server"
    assert imported["checker"] == {"value": "frozen checker", "source": str(runtime / "controller/check_ui/agent_checker.py")}
    assert json.loads((runtime / "server/config/example/example.json").read_text()) == {"fixture": "frozen asset"}


@pytest.fixture
def sealed_attempt(tmp_path, monkeypatch):
    baseline = b'{"historical": true}'
    (tmp_path / "baseline.json").write_bytes(baseline)
    plan = {"sessions": [{"id": "scene"}],
            "study": {"baseline_inputs": {"baseline.json": pilot.r.digest(baseline)}}}
    manifest = {"status": "completed", "scenes": {"scene": {"generation": 1}}}
    pilot.r.save_json(tmp_path / "manifest.json", manifest)
    pilot.r.save_json(tmp_path / "plan.json", plan)
    pilot.r.save_json(tmp_path / "results.json", [{"fixture": "saved score"}])
    calls = []
    monkeypatch.setattr(pilot.rp, "verify_archive", lambda r, path: calls.append("archive"))

    def scene_verify(r, attempt, plan, selected, record):
        assert r.DATA == tmp_path / "runtime/data"
        calls.append("scene")

    monkeypatch.setattr(pilot.rp, "verify_scene", scene_verify)
    monkeypatch.setattr(pilot.r, "summarize", lambda *args: {"valid_complete_run": True})
    pilot.seal(tmp_path)
    return tmp_path, calls


def test_intact_seal_verifies_and_restores_data_path(sealed_attempt):
    path, calls = sealed_attempt
    original_data = pilot.r.DATA
    original_root = pilot.r.ROOT
    assert pilot.verify(path)["valid_complete_run"] is True
    assert pilot.r.ROOT == original_root
    assert calls == ["archive", "scene"]
    assert pilot.r.DATA == original_data


def test_resealed_baseline_must_still_match_frozen_plan(sealed_attempt):
    path, _ = sealed_attempt
    (path / "baseline.json").write_text('{"changed": true}')
    pilot.seal(path)
    with pytest.raises(ValueError, match="historical comparison differs"):
        pilot.verify(path)


@pytest.mark.parametrize("change", ["modified", "added", "deleted"])
def test_seal_detects_evidence_tampering_before_scoring(sealed_attempt, change):
    path, calls = sealed_attempt
    if change == "modified":
        (path / "results.json").write_text("[]")
    elif change == "added":
        (path / "extra.json").write_text("{}")
    else:
        (path / "results.json").unlink()
    with pytest.raises(ValueError, match="Sealed attempt files changed"):
        pilot.verify(path)
    assert not calls


def test_verify_rejects_replayed_generation_even_with_fresh_seal(sealed_attempt):
    path, calls = sealed_attempt
    manifest = pilot.r.read_json(path / "manifest.json")
    manifest["scenes"]["scene"]["generation"] = 2
    pilot.r.save_json(path / "manifest.json", manifest)
    pilot.seal(path)
    original_data = pilot.r.DATA
    with pytest.raises(ValueError, match="first attempts only"):
        pilot.verify(path)
    assert pilot.r.DATA == original_data and calls == ["archive"]
