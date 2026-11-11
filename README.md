# FlockCue

FlockCue is an agent harness for coordinating multiple UAVs. The agent uses tool receipts and the fleet’s execution state to plan its next action. The project uses real DeepSeek trajectories to train a 2B commander and evaluates task pass rate under the same harness before training, after SFT and after GRPO. [中文](README_CN.md) · [Research roadmap](docs/RESEARCH_ROADMAP_CN.md)

## Repository

| Component | Directory |
| --- | --- |
| FlockCue runtime, execution memory, evidence and tools | [flockcue/](flockcue/README.md) |
| Agent4Drone service and upstream integrations | [agent4drone/](agent4drone/README.md) |
| Formal system evaluation | [flockcue/evaluation/](flockcue/evaluation/) |
| Teacher trajectory reconstruction | [posttraining/trajectories/](posttraining/trajectories/) |
| Student formatting, tokenizer checks and SFT | [posttraining/sft/](posttraining/sft/) |
| Online RL and GRPO | [posttraining/rl/](posttraining/rl/) |
| Original and frozen benchmarks, teacher and SFT data | [datasets/](datasets/README.md) |
| Experiment manifests and research reports | [experiments/](experiments/README.md) · [docs/](docs/README.md) |

The simulator and independent checker live in [server/](server/README.md), the scenario controller in [controller/](controller/README.md), and the 3D viewer in [view3d/](view3d/README.md). Shared environment and deployment scripts live in [scripts/](scripts/README.md).

## Run locally

Install the per-component environments using the [setup guide](docs/DEPENDENCY_SETUP_CN.md), then run these offline checks from the repository root.

```bash
agent4drone/.venv/bin/python -m flockcue check
python3 datasets/flockcue-600/verify.py --historical-csv
```

FlockCue + DeepSeek passed 544/600 tasks on the formal first pass. On 200 matched tasks with the same DeepSeek service fingerprint, FlockCue passed 182 and Agent4Drone passed 168. The first teacher release contains 386 reviewed decisions from 110 training tasks. Student SFT and GRPO have yet to run. [Formal evaluation](docs/results/flockcue-deployment600-20261111/REPORT_CN.md) · [Matched comparison](docs/results/agent4drone-matched200-20261111/REPORT_CN.md) · [Teacher release](docs/results/flockcue-teacher-trajectories-a-20261111/REPORT_CN.md)

## Versions and data

Edit the canonical module in place and use Git commits or tags to identify code revisions. The tag `project-layout-before-20261111` preserves the code before the directory move. Released protocol IDs and byte-level manifests retain their published names. Git includes the benchmark, manifests and reviewed teacher subset. The full teacher traces and run archives are stored locally and checked against their published hashes. [Migration notes](docs/maintenance/project-layout-20261111.md) · [Upstream components and citation](docs/upstream/README.md) · [License](LICENSE)
