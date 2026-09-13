# Coordinator Execution Guidelines

1. Execute steps in dependency order; skip steps whose dependencies failed.
2. Pass the full sub-task instruction markdown to each worker — not the bare summary.
3. On verifier INVALID: retry the worker up to 2 times with the verifier reason as repair hint.
4. Accumulate working memory with step headers instead of overwriting prior outputs.
5. After all steps pass verification, assemble the final deliverable per output_type rules.
6. Block delivery if any critical step remains unverified after retries.
