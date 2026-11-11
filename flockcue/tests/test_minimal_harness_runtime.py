"""Real service env/settings and trace serialization, using the existing offline probe."""
from test_m1_revision_runtime import test_service_environment_activates_revision_and_records_it as service_probe


def test_harness_service_mode_preserves_r2_evidence_and_writes_trace(monkeypatch, tmp_path):
    service_probe(monkeypatch, tmp_path, "evidence_harness", "m1-r2")
