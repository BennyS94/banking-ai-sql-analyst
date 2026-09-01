# NL-to-SQL evaluation

Live evaluation is an explicit operation and is never part of normal `pytest`
execution. Configure the least-privilege banking reader and `GROQ_API_KEY`, then
run the complete tracked benchmark with:

```powershell
python -m backend.app.evaluation
```

The command defaults to the Phase 6-selected `openai/gpt-oss-20b` model. Pass
`--model openai/gpt-oss-120b` explicitly to reproduce the evaluated comparison
model run.

Use `--case-id CASE_ID` repeatedly or `--category CATEGORY` to select a subset.
Each completed case is atomically persisted under the ignored
`evaluation/results/` directory. Resume an interrupted compatible run without
repeating completed stable case IDs:

```powershell
python -m backend.app.evaluation --resume evaluation/results/RUN_ID.json
```

This example assumes a 20B artifact; when resuming a 120B comparison artifact,
repeat `--model openai/gpt-oss-120b`.

Resume rejects changes to the model, reasoning effort, selected benchmark,
prompt/context fingerprint, statement timeout, or evaluation row limit.
Transient provider timeouts, unavailability, and rate limits stop the run before
the affected case is persisted, so resuming retries that case while retaining
all prior completed cases.
On completion, the command writes deterministic `evaluation_summary.json` and
`evaluation_report.md` files in a run-named directory beside the raw artifact.
An existing raw run can be rendered again without provider or database access:

```powershell
python -m backend.app.evaluation.reporting evaluation/results/RUN_ID.json
```

Reports include generation, semantic-status, safety, execution, result, and
end-to-end accuracy with explicit denominators; repair, latency, token, failure,
language, difficulty, category, and expected-status detail. Each new run also
records a deterministic safety snapshot from the stable Phase 4 adversarial
corpus and the trusted answerable benchmark SQL, keeping attack block rate and
legitimate-query false-positive rejection rate separate.

Compare one complete controlled run for each candidate with:

```powershell
python -m backend.app.evaluation.model_comparison `
  --run evaluation/results/GPT_OSS_20B_RUN.json `
  --run evaluation/results/GPT_OSS_120B_RUN.json
```

The comparison rejects partial runs, mismatched benchmark coverage, prompt
fingerprints, reasoning effort, or generation configuration. Optional repeated
subset runs can be supplied with repeated `--stability-run` arguments, with
matching coverage and repeat counts required for both models. The generated
technical recommendation records measured comparison evidence but does not
mutate the configured default model.
Generated SQL always passes structural validation, banking access validation,
and the hardened read-only PostgreSQL executor. Trusted project-authored
reference SQL uses the read-only executor separately. Correctness compares
normalized scalar, ordered-row, or unordered-row results and never SQL strings;
unordered comparison preserves duplicate-row multiplicity. Column aliases are
not part of result correctness, but column count and row shape are.
