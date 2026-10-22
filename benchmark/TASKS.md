# Benchmark protocol

Each candidate starts from the same frozen baseline SHA.

## Task 1
Fix the existing `discounted_price` function to apply the percentage before rounding down to integer cents. Keep the signature and validation rules. Include tests for prices below 100 cents and prices not divisible by 100.

## Task 2
SQLite price history with a frozen API and independent tests (to be specified before running).

## Task 3
Issue to PR workflow, error handling and accurate status reporting.

## Task 4
Multi-file feature with backwards compatibility and protected paths.

## Fairness
Keep agent-written tests separate from independent acceptance tests. Log model ID, attempts, manual interventions, duration and token usage where available. No secrets in the repository.
