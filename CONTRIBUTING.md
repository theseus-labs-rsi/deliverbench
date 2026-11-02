# Contributing

Use an issue to describe a reproducible software or task-definition problem. Include
the code version, dataset commit, project ID, and a minimal synthetic example. Do not
include credentials, private material, model execution traces, or benchmark results.

Keep changes to task semantics separate from execution-layer fixes. Changes to
instructions, source materials, rubrics, weights, dependencies, or scoring behavior
require an explicit version change and compatibility note. Preserve prior releases.

Run the offline release checks and the affected Docker fixture checks before a pull
request. Do not use paid model calls as a default unit test. Contributions to code are
under Apache-2.0; dataset-content contributions are under CC BY 4.0.
