"""Check the deployed SDK wire against the October settings and C sense ABI."""
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import Thread

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "agent4drone")]
from flockcue_deployment_agent import DeploymentDeepSeek
from tool_input_contract import adapt_input_json_tools
from uav_langchain_tools import create_uav_tools


def test_default_wire_retry_sense_and_reasoning_history():
    requests = []

    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if len(requests) == 1:
                status, response = 503, {"error": {"message": "temporary outage"}}
            else:
                if len(requests) == 2:
                    message = {"role": "assistant", "content": None, "reasoning_content": "Inspect the fleet.",
                               "tool_calls": [{"id": "sense-id", "type": "function",
                                               "function": {"name": "sense_nearby_entities", "arguments": "{}"}}]}
                else:
                    message = {"role": "assistant", "content": "Done."}
                status, response = 200, {"id": "sample", "model": "deepseek-flash", "object": "chat.completion",
                                         "created": 1, "choices": [{"index": 0, "message": message,
                                         "finish_reason": "tool_calls" if "tool_calls" in message else "stop"}]}
            raw = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    with ThreadingHTTPServer(("127.0.0.1", 0), Peer) as peer:
        thread = Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        try:
            kwargs = {"model": "deepseek-flash", "temperature": 0.1, "api_key": "local-fixture",
                      "base_url": f"http://127.0.0.1:{peer.server_port}"}
            llm = DeploymentDeepSeek(**kwargs)
            native = ChatOpenAI(**kwargs)
            sense, = (tool for tool in create_uav_tools(object()) if tool.name == "sense_nearby_entities")
            bound = llm.bind_tools(adapt_input_json_tools([sense], "json_object_or_string_v2"))
            task = HumanMessage("Sense the environment.")
            first = bound.invoke([task])
            assert first.tool_calls[0]["args"] == {"input_json": {}}
            assert first.tool_calls[0]["id"] == "sense-id"
            second = bound.invoke([task, first, ToolMessage("Observed.", tool_call_id="sense-id")])
            assert second.content == "Done."
            assert len(requests) == 3 and requests[0] == requests[1]
            # Absolute parity with the archived native constructor for all
            # request settings; only the policy's tool definition is changed.
            native_payload = native._get_request_payload([task])
            assert {k:v for k,v in requests[0].items() if k != "tools"} == native_payload
            assert llm.root_client.max_retries == native.root_client.max_retries == 2
            offered, = requests[0]["tools"]
            assert not offered["function"]["parameters"].get("required")
            assert sha256(offered["function"]["description"].encode()).hexdigest() == (
                "27f62012a8b37dd0b92e97cb33c93a015e6b573432bd4c98a8f8296902848227")
            echo, receipt = requests[2]["messages"][-2:]
            assert echo["reasoning_content"] == "Inspect the fleet."
            assert receipt["tool_call_id"] == echo["tool_calls"][0]["id"] == "sense-id"
            assert json.loads(echo["tool_calls"][0]["function"]["arguments"]) == {"input_json": {}}
        finally:
            peer.shutdown()
            thread.join()
