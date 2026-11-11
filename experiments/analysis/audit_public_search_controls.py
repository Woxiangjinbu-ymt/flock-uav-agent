#!/usr/bin/env python3
"""Check text-only activation on matching R2 progress tasks.

Task text and initial progress are read through public server APIs. Original
outcomes are joined only afterward from the frozen check files.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from api.server import ROLE_SECRETS, UserRole, app  # noqa: E402

RUN = ROOT / "experiments/runs/20261007-211050-m1r2-fast100-f5985403"
OUT = ROOT / "docs/audit/r2-timed-public-search-20261009/control-audit.json"
IDS = ("9c0b52a0", "7eba7390", "b1ad4f3e",
       "5e743d21", "24ac1838", "7cdefb56")


def main():
    admin = {"X-API-Key": ROLE_SECRETS[UserRole.ADMIN]}
    agent = {"X-API-Key": ROLE_SECRETS[UserRole.AGENT]}
    rows = []
    with TestClient(app) as client:
        for tid in IDS:
            paths = list(RUN.glob(f"*/{tid}.before.json"))
            if len(paths) != 1:
                raise ValueError(f"Expected one task-start snapshot for {tid}")
            before = json.loads(paths[0].read_text())
            session_id = before["id"]
            client.post(f"/sessions/{session_id}", headers=admin,
                        json=before).raise_for_status()
            client.post(f"/sessions/{session_id}/set-current",
                        headers=admin).raise_for_status()
            task_response = client.get(f"/sessions/current/tasks/{tid}", headers=agent)
            progress_response = client.get("/sessions/current/task-progress", headers=agent)
            task_response.raise_for_status()
            progress_response.raise_for_status()
            task = task_response.json()
            progress = progress_response.json()["progress_percentage"]
            wording_matches = ("session task progress exceeds 95%" in
                                " ".join(task["content"].lower().split()) and
                                "all targets" in task["content"].lower())
            checks = json.loads(paths[0].with_name(f"{tid}.checks.json").read_text())
            leaves = [item for item in checks if item["type"] == "leaf"]
            if len(leaves) != 1 or leaves[0]["endpoint"] != "/check/task_progress":
                raise ValueError(f"Unexpected original check for {tid}")
            rows.append({"task_id": tid, "scene_id": session_id,
                         "public_task_text_matches": wording_matches,
                         "initial_public_progress_percent": progress,
                         "naive_activation_at_task_start": wording_matches and progress <= 95,
                         "original_r2_check_passed": bool(leaves[0]["result"])})
    output = {"schema": 1, "source": str(RUN.relative_to(ROOT)),
              "only_public_text_and_progress_for_activation": True,
              "rows": rows}
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
