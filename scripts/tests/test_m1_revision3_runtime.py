"""The existing real service entry also accepts opt-in R3 without any I/O."""
import pytest
from test_m1_revision_runtime import test_service_environment_activates_revision_and_records_it as service_probe


@pytest.mark.parametrize("mode", ["evidence_r3", "evidence_r3_shadow"])
def test_service_environment_activates_r3_and_keeps_r2_visit_evidence(monkeypatch, tmp_path, mode):
    service_probe(monkeypatch, tmp_path, mode, "m1-r3")
