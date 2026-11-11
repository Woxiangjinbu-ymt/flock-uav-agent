"""Bounded local regression runner; no provider, simulator campaign or training."""
from pathlib import Path
import contextlib, hashlib, json, os, socket, sys, time
import pytest
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path[:0] = [str(ROOT/'agent4drone'), str(ROOT/'agent4drone/tests')]
os.environ.pop('UAV_MODEL_IO_ARCHIVE', None)
os.environ.pop('UAV_MODEL_IO_ARCHIVE_DIR', None)
def forbidden(*args, **kwargs):
    raise AssertionError('Network forbidden in model-I/O regression')
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = forbidden
FILES = ['agent4drone/tests/test_minimal_harness.py', 'agent4drone/tests/test_harness_entry_contract.py', 'agent4drone/tests/test_harness_flight_state.py', 'agent4drone/tests/test_harness_blocked_candidate.py', 'agent4drone/tests/test_execution_memory_integration.py', 'agent4drone/tests/test_execution_memory_v2_integration.py', 'agent4drone/tests/test_execution_memory_v3_integration.py', 'agent4drone/tests/test_execution_memory_v4_integration.py', 'agent4drone/tests/test_execution_memory_v5_integration.py', 'agent4drone/tests/test_execution_memory_v6_integration.py', 'agent4drone/tests/test_execution_memory_v7_integration.py', 'agent4drone/tests/test_harness_shared_endpoint.py', 'agent4drone/tests/test_harness_terminal_progress.py', 'agent4drone/tests/test_harness_observed_target.py', 'agent4drone/tests/test_agent_api_service.py', 'agent4drone/tests/test_task_evidence.py', 'agent4drone/tests/test_task_evidence_r2.py', 'agent4drone/tests/test_task_evidence_r3.py', 'agent4drone/tests/test_task_progress.py', 'agent4drone/tests/test_task_progress_semantics.py', 'agent4drone/tests/test_token_usage.py', 'agent4drone/tests/test_uav_api_client.py', 'agent4drone/tests/test_agent_episode_isolation.py', 'agent4drone/tests/test_task_progress_integration.py']
SOURCES = FILES + ['agent4drone/uav_agent.py','agent4drone/model_io_archive.py','agent4drone/model_request_audit.py','agent4drone/toolchain_recorder.py']
def hashes():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
log = OUT/'regression-existing.log'
metadata = OUT/'regression-existing.json'
if log.exists() or metadata.exists(): raise RuntimeError('Refuse to overwrite previous execution')
before = hashes(); start=time.monotonic()
with log.open('x') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
    code = pytest.main([*FILES, '-q'])
after = hashes()
result = {'test_files':FILES,'source_sha256_before':before,'source_sha256_after':after,'source_unchanged':before==after,'exit_code':int(code),'elapsed_seconds':time.monotonic()-start,'network':'socket connect/connect_ex/create_connection denied','scope':'Existing regressions only; no provider or simulator benchmark'}
metadata.write_text(json.dumps(result,indent=2)+'\n')
print(log.read_text()[-1400:]); print(json.dumps({'exit_code':int(code),'source_unchanged':before==after,'file_count':len(FILES)}))
sys.exit(int(code))
