"""Exercise a provider retry sequence missing one wire response, entirely offline."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "scripts")))
import report_flockcue_deployment600 as report


def blob(data):
    return {'base64': base64.b64encode(data).decode(), 'length': len(data),
            'sha256': hashlib.sha256(data).hexdigest()}


class DeploymentReporterTests(unittest.TestCase):
    def test_transport_error_does_not_shift_or_duplicate_retried_usage(self):
        wire = []
        metrics = []
        timestamp = 1794376629.0

        def event(kind, **data):
            wire.append({'command_id': 'command', 'kind': kind, 'data': data})

        def metric(kind, **data):
            metrics.append({'event': kind, 'job_id': 'job', 'run_id': 'logical', 'timestamp': timestamp,
                            **data})

        event('archive_start')
        metric('llm_start', command_id='command')
        usage = {'prompt_tokens': 35, 'completion_tokens': 5, 'total_tokens': 40,
                 'completion_tokens_details': {'reasoning_tokens': 2}}
        # A rate limit, a failed connection, then a successful SDK retry.
        for index, code in enumerate([429, None, 200], 1):
            attempt = f'attempt-{index}'
            request = f'request-{index}'
            event('http_request', attempt_id=attempt, body=blob(b'{"model":"deepseek-flash"}'))
            metric('http_start', request_id=request, retry_index=index-1)
            if code is None:
                event('http_response_end', attempt_id=attempt, complete=False)
                metric('http_error', request_id=request, error_type='ConnectError')
                continue
            body = json.dumps({'usage': usage, 'model': 'deepseek-flash'} if code == 200 else {'error': 'limited'}).encode()
            capture = blob(body)
            event('http_response_start', attempt_id=attempt, status_code=code, body_scope='buffered_httpx_content')
            event('http_response_chunk', attempt_id=attempt, body=capture)
            event('http_response_end', attempt_id=attempt, complete=True, length=len(body), sha256=capture['sha256'])
            metric('http_end', request_id=request, status_code=code)
        event('archive_end', complete=False, reason='completed', reasons=['response_incomplete'])
        metric('llm_end', command_id='command')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            modelio = folder / 'agent-traces/modelio'
            modelio.mkdir(parents=True)
            archive = modelio / 'model-io-example.jsonl'
            archive.write_text(''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in wire))
            (folder / report.METRICS_FILE).write_text(''.join(json.dumps(r) + '\n' for r in metrics))
            _, receipts, calls = report.telemetry(folder)
        total = report.cost_summary(calls)
        self.assertEqual(total['http_dispatched'], 3)
        self.assertEqual(total['http_retries'], 2)
        self.assertEqual(total['http_with_usage'], 1)
        self.assertEqual(total['http_with_wire_response_facts'], 2)
        self.assertEqual(total['total_tokens'], 40)
        self.assertEqual(total['reasoning_tokens'], 2)
        self.assertEqual(calls[1]['http_error'], 'ConnectError')
        self.assertEqual(calls[2]['total_tokens'], 40)
        self.assertEqual(receipts['job']['issues'], {'incomplete_response': 1})
