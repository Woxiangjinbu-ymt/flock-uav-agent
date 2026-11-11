"""Pure-object review probes. No provider, HTTP send, world, checker, or pytest."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "agent4drone"))
import httpx
from langchain_core.messages import AIMessage, ToolMessage
from model_io_archive import (ModelIOArchive, _safe, archive_response,
                              request_is_ambiguous, verify_archive)


def forbidden(*args, **kwargs):
    raise AssertionError("No network in independent pure-object probes")


socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
CHECKS = []


def check(name, predicate):
    CHECKS.append({"name": name, "passed": bool(predicate)})
    assert predicate, name


def begin(folder, name):
    archive = ModelIOArchive(name, folder)
    call = archive.begin_model(SimpleNamespace(system_message=None, messages=[]))
    request = httpx.Request("POST", "https://offline.invalid/route?key=SYNTHETIC_QUERY",
                            headers={"Authorization": "SYNTHETIC_AUTH"},
                            content=b'{"messages":[],"seed":17}')
    archive.request(request, call)
    return archive, call, request


def complete_response(archive, call, request):
    archive_response(httpx.Response(200, request=request, content=b'{"public":true}',
                                   headers={"x-secret": "SYNTHETIC_RESPONSE_HEADER"}))
    archive.end_model(call, AIMessage(content="public"))


class DualStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self):
        self.reads, self.closed = 0, False

    def __iter__(self):
        for item in (b"part1", b"part2"):
            self.reads += 1
            yield item

    async def __aiter__(self):
        for item in (b"part1", b"part2"):
            self.reads += 1
            yield item

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


with tempfile.TemporaryDirectory() as folder:
    a = ModelIOArchive("projection", folder)
    safe_message = a._serialize(AIMessage(content="visible", response_metadata={
        "headers": {"Authorization": "SYNTHETIC_AUTH"}}))
    safe_tool = a._serialize(ToolMessage(content="public", tool_call_id="id", artifact={
        "private_checker": "SYNTHETIC_PRIVATE"}))
    check("nonvisible_metadata_and_artifact_excluded", "SYNTHETIC_" not in json.dumps([safe_message, safe_tool]))
    try:
        _safe(a, lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    except KeyboardInterrupt:
        propagated = True
    else:
        propagated = False
    check("observer_keyboard_interrupt_propagates", propagated)
    check("ordinary_observer_exception_is_fail_open", _safe(a, lambda: 1 / 0) is None)
    a.close()

    for exc_type in (RuntimeError, KeyboardInterrupt):
        a, call, request = begin(folder, "close-" + exc_type.__name__)
        complete_response(a, call, request)
        original = a._append
        def fail_end(kind, data):
            if kind == "archive_end":
                raise exc_type()
            return original(kind, data)
        a._append = fail_end
        propagated = False
        try:
            a.close()
        except KeyboardInterrupt:
            propagated = True
        check("close_" + exc_type.__name__ + "_never_claims_complete", not a.info()["complete"])
        check("close_" + exc_type.__name__ + "_preserves_exception_policy", propagated == (exc_type is KeyboardInterrupt))

    a, ca, shared = begin(folder, "reuse-A")
    b = ModelIOArchive("reuse-B", folder)
    cb = b.begin_model(SimpleNamespace(system_message=None, messages=[]))
    check("request_rebind_is_rejected", b.request(shared, cb) is None and request_is_ambiguous(shared))
    sa, sb = a.info()["sequence"], b.info()["sequence"]
    archive_response(httpx.Response(200, request=shared, content=b"LATE_A_BODY"))
    check("quarantined_response_writes_neither_archive", (a.info()["sequence"], b.info()["sequence"]) == (sa, sb))
    a.close(); b.close()
    check("request_reuse_marks_both_incomplete", not a.info()["complete"] and not b.info()["complete"])

    a, ca, ra = begin(folder, "closed-A")
    complete_response(a, ca, ra)
    a.close()
    old_hash = hashlib.sha256(Path(a.path).read_bytes()).hexdigest()
    b, cb, rb = begin(folder, "current-B")
    before = b.info()["sequence"]
    archive_response(httpx.Response(200, request=ra, content=b"LATE_CLOSED_BODY"))
    check("late_closed_owner_leaves_both_unchanged", old_hash == hashlib.sha256(Path(a.path).read_bytes()).hexdigest() and before == b.info()["sequence"])
    complete_response(b, cb, rb)
    b.close()
    saved = Path(b.path).read_text()
    check("headers_and_query_never_archived", not any(value in saved for value in ("SYNTHETIC_AUTH", "SYNTHETIC_QUERY", "SYNTHETIC_RESPONSE_HEADER")))
    check("valid_complete_wire_roundtrip", verify_archive(b.path)["valid"] and verify_archive(b.path)["complete"])

    for asynchronous in (False, True):
        a, call, request = begin(folder, "dual-" + str(asynchronous))
        stream = DualStream()
        response = httpx.Response(200, request=request, stream=stream)
        archive_response(response)
        check("dual_hook_no_preread_" + str(asynchronous), stream.reads == 0)
        if asynchronous:
            result = asyncio.run(response.aread())
        else:
            result = response.read()
        check("dual_protocol_preserved_" + str(asynchronous), result == b"part1part2" and stream.reads == 2 and stream.closed)
        a.end_model(call, AIMessage(content="public"))
        a.close()
        check("dual_complete_" + str(asynchronous), verify_archive(a.path)["complete"])

    a, call, request = begin(folder, "partial")
    stream = DualStream()
    response = httpx.Response(200, request=request, stream=stream)
    archive_response(response)
    iterator = response.iter_raw()
    check("partial_first_chunk_preserved", next(iterator) == b"part1")
    response.close()
    a.end_model(call, AIMessage(content="public"))
    a.close()
    check("early_close_incomplete_without_drain", stream.reads == 1 and not a.info()["complete"] and not verify_archive(a.path)["complete"])

sources = ["agent4drone/model_io_archive.py", "agent4drone/uav_agent.py", "agent4drone/model_request_audit.py"]
print(json.dumps({"scope": "pure_object_review_probes_not_pytest_or_campaign", "network": "blocked",
                  "passed": sum(c["passed"] for c in CHECKS), "checks": CHECKS,
                  "sources": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}},
                 ensure_ascii=False, indent=2))
