"""Run the registered FlockCue policy service or its offline checks."""
import argparse
import json
from pathlib import Path


def main():
    import sys
    if sys.argv[1:2] == ["serve"]:
        from .service import main as serve
        del sys.argv[1]
        return serve()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="run the policy HTTP service")
    commands.add_parser("check", help="validate the registered profile and public tool contract")
    smoke = commands.add_parser("smoke", help="exercise a real benchmark task with a loopback model fixture")
    smoke.add_argument("--output", required=True, type=Path, help="new directory for traces and independent checks")
    args = parser.parse_args()
    if args.command == "smoke":
        from .smoke import run
        result = run(args.output.resolve())
    else:
        from .profile import PROFILE_SHA256, load_profile
        from .provider import effective_tools, SENSE
        from .tools.catalog import create_uav_tools
        from .tools.inputs import adapt_input_json_tools
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from hashlib import sha256
        tools = adapt_input_json_tools(create_uav_tools(object()), "json_object_or_string_v2")
        wire = effective_tools([convert_to_openai_tool(tool) for tool in tools])
        sense, = (t["function"] for t in wire if t["function"]["name"] == SENSE)
        if sha256(sense["description"].encode()).hexdigest() != "27f62012a8b37dd0b92e97cb33c93a015e6b573432bd4c98a8f8296902848227":
            raise ValueError("Public sensing tool differs from the registered deployment")
        result = {"profile": load_profile()["profile_id"], "profile_sha256": PROFILE_SHA256,
                  "base_tools": len(wire), "sense_interface": "C", "provider_calls": 0}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
