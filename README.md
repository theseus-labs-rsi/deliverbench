# DeliverBench

DeliverBench evaluates agents on continuous professional projects. An agent works in
one persistent session, produces intermediate documents, receives new materials and
responses based on its own work, and delivers a final package.

This release provides the execution and scoring code for three public projects:
finance (P02, 13 stages), pharmacovigilance (P08, 12 stages), and legal delivery
(P10, 8 stages). Each project has a separate FINAL evaluation.

[Dataset](https://huggingface.co/datasets/deliverbench/DeliverBench) ·
[中文说明](README.zh-CN.md)

## Quick start

Requirements: Python 3.12, uv, Docker with Compose, and at least 8 GiB allocated to
Docker. Image builds download public dependencies. Allow additional disk space for
images and your own run artifacts.

```sh
git clone https://github.com/theseus-labs-rsi/deliverbench.git
cd deliverbench
git checkout v3.1.1
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python uv 'git+https://github.com/theseus-labs-rsi/deliverbench.git@v3.1.1#subdirectory=deliverbench-harbor' -r requirements-tools.txt
source .venv/bin/activate
python scripts/download_data.py --project all --output-dir data
harbor --version
harbor plugins list
```

The downloader pins a dataset commit in `data_release.json` and verifies every
project file. `--project P02`, `P08`, or `P10` downloads one project. An identical
existing project is reused; changed files or extra files cause a refusal instead of
an overwrite. The optional `--source-dir` reads a previously downloaded Hub snapshot.

## Check the installation

Run the complete pipeline with authored reference fixtures and local synthetic
Judge/Responder services. No paid model API is used. The output directory must be new.

```sh
python data/02_project_mars_refinancing/qa/pipeline_probe.py --output "$PWD/checks/p02"
```

Use the corresponding project directory for P08 or P10. These checks exercise the
execution protocol; their synthetic judgments do not measure model performance.

## Run your model

Configure credentials in your shell; `.env.example` is a template and is not loaded
automatically. Candidate, Judge, and Responder calls may have separate costs.
The default Codex configuration requires a compatible Responses API. The Responder
uses structured Chat Completions and defaults to the Judge credentials.

```sh
export OPENAI_API_KEY='<candidate-api-key>'
export OPENAI_BASE_URL='<candidate-base-url>'
export JUDGE_API_KEY='<judge-api-key>'
export JUDGE_BASE_URL='<judge-base-url>'
export JUDGE_MODEL='<judge-model>'
export RESPONDER_API=chat
cd data/02_project_mars_refinancing
harbor run -c harbor_job.yaml -a deliverbench_harbor.agents:Codex -m '<candidate-model>' --resume-trajectory --plugin deliverbench --job-name p02-my-model
deliverbench-check jobs/p02-my-model --verify-evidence
```

Model-specific configuration examples are included with each project. Inspect their
provider route and pinned tool version before use. Keep Judge and Responder settings
fixed when comparing agents. See [running and scoring](docs/RUNNING.md).

## Execution and scoring

The opening background is unscored. Stage inputs are released at their designated
time; the agent carries its own artifacts forward. Authored reference files, rubrics,
future materials, and simulator internals are not made available in the Candidate
workspace by the runner. Public availability of those files does not permit an
evaluation agent to retrieve them from the public repository.

Each scored unit freezes its evidence and receives complete criterion judgments.
Process and FINAL evaluations assess different submitted versions; FINAL does not
average process scores or inherit previous hurdle hits. Missing judgments, unreadable
evidence, and workflow errors are not converted into valid zero scores. A genuine
hurdle violation can produce a valid zero. See [the scoring contract](deliverbench-harbor/README.md).

The public repositories contain task definitions and software only. They contain no
model submissions, benchmark scores, judgments, execution traces, or run reports.

## Development

`deliverbench-harbor/qa` contains protocol tests. Run
`python scripts/check_release.py --data-dir data --output-dir checks/release`
from the repository root in the environment above. This runs offline checks; use each
project's `pipeline_probe.py` separately for Docker integration checks. Generated
reports and run artifacts remain in your local output directory.

The dataset is version 3.1.1; the shared extension is 0.7.2 and requires stock Harbor
0.20.0. This project does not vendor Harbor or require private container images.

## License and citation

Code is licensed under [Apache-2.0](LICENSE). Dataset content is CC BY 4.0; executable
files shipped with the dataset are Apache-2.0. See the dataset's licensing notice.
[CITATION.bib](CITATION.bib) provides software and dataset references.
