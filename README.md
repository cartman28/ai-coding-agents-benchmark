# AI Coding Agents Benchmark

Fair comparison of autonomous coding agents using GitHub Issues, pull requests and independent acceptance tests.

## Candidate order
1. OpenHands SDK + Gemini Flash-Lite
2. Gemini CLI + free Gemini tier (subject to available quotas)
3. OpenCode + a compatible free model

## Principles
- Each agent starts from the same immutable benchmark base commit.
- One task per issue; human review required before merging.
- Agent-generated tests never substitute for independent acceptance checks.
- Record the model ID, authentication mode, run ID, elapsed time, tokens/cost when available, number of attempts and interventions.
- Never commit API keys, access tokens, or hidden evaluation cases to the candidate workspace.
- Keep benchmarks deterministic, without external services or network dependencies.

## Evaluation (100 points)
- Functional correctness: 30
- Reliability and regression safety: 20
- Autonomy: 20
- Code quality and simplicity: 15
- Resource efficiency: 10
- Reporting and lifecycle correctness: 5

A passing CI check does not mean a task is accepted. Issues close only after independent review and merge.

## Statuses
`ready` → `in-progress` → `review` → accepted/closed; alternatives: `needs-attention` and `failed`.

## Execution
The runner integration for each candidate is evaluated separately. Do not make the benchmark itself depend on any agent runner.
