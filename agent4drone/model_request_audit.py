"""Hash actual serialized provider requests, without storing headers or keys.

httpx request hooks run after SDK serialization, including SDK retries. This
observer does not send requests, consume responses, or change transport policy.
"""
import hashlib
import json


def attach_request_hooks(model, sync_hook, async_hook):
    # Keep the exact clients LangChain created (timeouts, proxy, connection
    # limits, socket settings, retry behavior). Only extend public hook lists.
    # Access to the SDK's underlying client is tested against the pinned SDK;
    # missing access fails open and is explicitly logged as setup unavailable.
    clients = (model.root_client._client, model.root_async_client._client)
    for client, hook in zip(clients, (sync_hook, async_hook)):
        client.event_hooks["request"].append(hook)


def request_record(request):
    body = request.content
    payload = json.loads(body)
    messages = payload.get("messages", [])
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode()).hexdigest()
    return {"scope": "serialized_http_request_body", "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_bytes": len(body), "method": request.method, "path": request.url.path,
            "message_sha256": [digest(m) for m in messages],
            "tools_sha256": digest(payload.get("tools", [])),
            "model": payload.get("model"), "stream": payload.get("stream", False)}
