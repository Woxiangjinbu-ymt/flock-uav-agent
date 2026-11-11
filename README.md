# FlockCue: Multi-UAV Coordination with Execution Feedback

**FlockCue** is an agent harness for multi-UAV coordination with execution feedback. The agent picks tools and directs the fleet. FlockCue records which UAV acted, what each receipt confirmed, and which task requirements still lack evidence. After evaluating the harness, the project will use verified DeepSeek trajectories to train a ~2B commander with SFT and RL and measure its task pass rate. [中文](README_CN.md) · [Run FlockCue](agent_harness/README.md)

## Current work · 2026-11-11

The formal evaluation uses 600 frozen tasks from 30 complete scenarios and the original 1,500-task deployment settings. The historical Agent4Drone run with DeepSeek passed 506 of those 600 tasks. FlockCue’s results will inform which modules to keep. [Evaluation specification](docs/specs/05-deepseek-harness-ab600/spec.md) · [Benchmark and historical scores](experiments/flockcue-benchmark-600-v1/README_CN.md)

| Question | Start here |
| --- | --- |
| Research question and stages | Evaluate FlockCue, settle the harness design, then train and evaluate the student. [Roadmap](docs/RESEARCH_ROADMAP_CN.md) |
| What the student learns and how arms are compared | Verified teacher demonstrations and task pass rates before SFT, after SFT and after RL. [Post-training plan](docs/POSTTRAINING_PLAN_CN.md) |
| What already runs | The local v5 baseline replays; teacher reasoning usage in four sealed real batches has been recomputed. No student weights yet. [Status](docs/RESEARCH_STATUS_CN.md) · [Evidence index](docs/EXPERIMENT_EVIDENCE_CN.md) |
| Thesis, paper and demo | Differences from RACE and PenguinHarness, and what counts as a contribution. [Project guide](docs/PROJECT_GUIDE_CN.md) |

## Repository map

```text
agent_harness/ FlockCue research entry: version lock, check and smoke
agent4drone/   UAV agent, harness runtime, receipt parsing, model I/O archive
server/        Simulation, public APIs and isolated checkers
controller/    Scenario management and execution GUI
view3d/        3D viewer
benchmark/     Upstream tasks and scenes
scripts/       Runners, batch entry points, validation and audits (see scripts/README.md)
training/      Pinned tokenizer and input checks
experiments/   Frozen splits, profiles, fixtures and historical experiments (see experiments/README.md)
docs/          Current docs, specs, sealed results and history (see docs/README.md)
tmp/           Scratch; only task-splits-pilot-01 is kept for the split audit
tutorial/      Upstream platform walkthroughs
```

## Getting started

Set up the per-component environments with the [dependency guide](docs/DEPENDENCY_SETUP_CN.md). Platform startup is in the [runbook](docs/REPRODUCTION_RUNBOOK_CN.md); see also the [English tutorial](tutorial/tutorial_en.md). Keep provider credentials in ignored local settings.

Verify the research baseline from the repository root:

```bash
agent4drone/.venv/bin/python -m agent_harness check
```

## Attribution and history

The simulator, benchmark, Agent4Drone foundation and viewer come from MultiUAV-Plat. Local changes and their evidence are recorded separately. [Upstream documentation and citation](docs/upstream/README.md) · [License](LICENSE)

The 2026-11-08 cleanup moved development scratch output out of the repository and moved the superseded verifier draft and budget into `docs/history/`. See the [cleanup manifest](docs/maintenance/cleanup-20261108.json) and the earlier [2026-10-28 manifest](docs/maintenance/cleanup-20261028.json).
