# UAV Agent API Service

This service provides a REST API for the UAV Control Agent. It allows external applications to send natural language commands to the drone fleet and receive feedback, reasoning steps, and mission status.

## 🚀 Getting Started

### Prerequisites

Use a dedicated Python 3.11 environment. From the repository root, `bash scripts/setup_python_envs.sh --dev` installs the project-local environments on macOS/Linux. Alternatively, inside this directory create and activate a Python 3.11 virtual environment, then install the complete locked runtime dependencies:

```bash
python -m pip install -r requirements.txt
```

The server and agent environments are separate because they require different Pydantic versions. See [dependency setup and validation](../docs/DEPENDENCY_SETUP_CN.md). For GUI mode, Python must include Tk; speech recognition dependencies are optional.

### Starting the Server

Run the server using the new service script:

```bash
python agent_api_service.py
```

The server will start on **port 18000** by default:
- **API Base URL:** `http://localhost:18000`
- **Interactive Documentation (Swagger):** `http://localhost:18000/docs`

---

## 🛠 API Reference

### Command isolation and reset

One `UAVControlAgent` instance admits one active operation. Concurrent or
reentrant `execute`, `reset_task_evidence`, `refresh_session_context`, and
`get_session_summary` calls are rejected before changing command state or making
API calls. Direct Python callers receive `AgentBusyError`. The synchronous
command and session-summary endpoints return HTTP 409 with `detail.code` set to
`agent_busy`.

Async submission does not reserve execution. If its background worker finds the
agent busy, that job becomes `failed` with an `agent_busy` error, without execution
or automatic retries. A job already marked `cancelled` keeps that status.

Cancellation is a request to stop: the active operation remains reserved until
execution and result recording exit. `cancelled` alone does not mean it is safe
to reload the shared world. For a submitted job, `completed_at` is set when its
worker finishes, including a cancellation detected before execution. Already
committed external actions are not rolled back by cancelling the agent.

An escaping `BaseException` closes the old Harness and stops its API recorder;
it does not create a completed result archive. If cleanup itself fails, discard
that agent instance: subsequent protected operations are refused. Ordinary
sequential execution and explicit evidence reset otherwise keep their behavior.

`reset_task_evidence` clears memory only. The caller must separately coordinate
world restoration and wait for all users of that world to stop. This per-instance
guard does not provide a global simulator lock, multiple-agent isolation, or a
complete training-environment reset. See the [episode contract validation](../docs/results/harness-episode-contract-20261024/REPORT_CN.md).

### 1. Asynchronous Execution (Recommended)
Since agent commands can take 1-3 minutes to process, use the async "Job" pattern to avoid timeouts.

**Step 1: Submit Command**
- **Endpoint:** `/agent/command/async`
- **Method:** `POST`
- **Body:** `{"command": "Take off drone-1..."}`
- **Response:**
  ```json
  {
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "queued",
    "message": "Command accepted for background processing"
  }
  ```

**Step 2: Check Status**
- **Endpoint:** `/agent/jobs/{job_id}`
- **Method:** `GET`
- **Response (while running):** `{"status": "running", ...}`
- **Response (completed):**
  ```json
  {
    "job_id": "...",
    "status": "completed",
    "result": {
      "success": true,
      "output": "Drone has taken off...",
      "intermediate_steps": [...] 
    }
  }
  ```

**Step 3: Cancel Job (Optional)**
Stop a running agent command.
- **Endpoint:** `/agent/jobs/{job_id}/cancel`
- **Method:** `POST`
- **Response:** `{"message": "Job ... cancellation requested."}`

### 2. Synchronous Execution (Blocking)
Useful for quick queries, but may timeout for complex tasks.

- **Endpoint:** `/agent/command`
- **Method:** `POST`
- **Body:** `{"command": "Get status"}`

### 3. Get Session Summary
Retrieve the current mission status.

- **Endpoint:** `/agent/session`
- **Method:** `GET`

### 4. Health Check
- **Endpoint:** `/health`
- **Method:** `GET`

---

## 💻 Integration Example (Python)

```python
import requests
import time

BASE_URL = "http://localhost:18000"

def run_async_agent_task(command):
    # 1. Submit Job
    print(f"🚀 Sending command: {command}")
    resp = requests.post(f"{BASE_URL}/agent/command/async", json={"command": command})
    if resp.status_code != 200:
        print(f"Error submitting: {resp.text}")
        return
        
    job_id = resp.json()["job_id"]
    print(f"⏳ Job submitted (ID: {job_id}). Waiting for results...")
    
    # 2. Poll for completion
    try:
        while True:
            status_resp = requests.get(f"{BASE_URL}/agent/jobs/{job_id}")
            job_info = status_resp.json()
            status = job_info["status"]
            
            if status == "completed":
                print("\n✅ Task Completed!")
                print(f"Output: {job_info['result']['output']}")
                break
            elif status == "failed":
                print(f"\n❌ Task Failed: {job_info.get('error')}")
                break
            elif status == "cancelled":
                print("\n🛑 Task Cancelled.")
                break
            else:
                print(f"   Status: {status}...", end="\r")
                time.sleep(2)
    except KeyboardInterrupt:
        # Cancel on Ctrl+C
        print("\n\n⚠️  Interrupted! Cancelling job...")
        requests.post(f"{BASE_URL}/agent/jobs/{job_id}/cancel")
        print("Cancellation sent.")

if __name__ == "__main__":
    run_async_agent_task("Take off drone-1 to 15 meters and inspect the area")
```

---

## ⚙️ Configuration

The server uses `llm_settings.json` for LLM provider configuration. 

**Environment Variables:**
- `UAV_API_URL`: Override UAV simulator URL (Default: `http://localhost:8000`)
- `UAV_API_KEY`: API key for the UAV simulator.
- `OPENAI_API_KEY` / `LLM_API_KEY`: Fallback API keys.

```
