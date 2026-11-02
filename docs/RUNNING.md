# Running DeliverBench

Install the extension and download the pinned dataset using the root README. Run
Harbor from the selected project directory so relative task and metric paths resolve.
Use a distinct job name for every run; keep your jobs outside the source repository
when sharing code. The default project configuration uses a 14,400-second Candidate
limit per stage. Inspect `task/task.toml` and the chosen YAML for all limits.

## Credentials and providers

Codex uses `OPENAI_API_KEY` and `OPENAI_BASE_URL`; the Judge uses `JUDGE_API_KEY`,
`JUDGE_BASE_URL`, and `JUDGE_MODEL`. Claude-compatible examples use
`ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL`. Qwen examples use the OpenAI-compatible
variables. Provider names in example YAML files are examples, not credentials or a
promise that a route is available to every user.

The Responder defaults to the Judge configuration. Set `RESPONDER_BASE_URL`,
`RESPONDER_API_KEY`, and `RESPONDER_MODEL` to use a separate compatible provider.
Compare agents under fixed task, Judge, Responder, tool-version, and reasoning settings.
Never enable synthetic fixture mode for a model evaluation.

## Evidence and failure semantics

The runner freezes intermediate submissions before later stages. Dynamic responses
are tied to the submitted input hashes and cannot silently replace a published
response. A resumed run keeps prior evidence and published responses; recovery is
not permission to regenerate a Candidate answer after a capability failure.

`jobs/<job>/delivery_summary.json` records workflow state. Each trial has stage
verifier outputs and frozen Candidate evidence; the last stage also has FINAL outputs.
These files are created by the user's run and are not included in this release.
Run `deliverbench-check jobs/<job> --verify-evidence` before interpreting scores.

Do not report a Candidate timeout as a scored submission, even if a later verifier
produces an audit reward. Distinguish valid zero scores from incomplete or failed
evaluation workflows. Do not automatically retry a timed-out or low-scoring Candidate.
Technical recovery of an existing job is available via `deliverbench-resume --help`;
it validates the existing evidence before resuming.

## Isolation

Task material is publicly downloadable for researchers, but the Candidate container
receives only the materials allowed at its current stage. Rubrics, authored references,
simulator internals, and unreleased future inputs belong to separate runner/verifier
views. Do not mount the dataset root or the code checkout into the Candidate workspace.
Do not allow an evaluated agent to fetch the public task repository to access hidden
materials. Public tasks should not be treated as a private held-out benchmark.

## Offline checks

`scripts/check_release.py` runs schema, dependency, language-hurdle, material-release,
response, and installation checks without calling model APIs. The three project
`qa/pipeline_probe.py` entry points run authored Oracle fixtures with local synthetic
services through Docker. Synthetic outputs test protocol behavior only. Keep all
generated checks, logs, and artifacts outside the published file set.
