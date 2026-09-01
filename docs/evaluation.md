# NL-to-SQL evaluation

Live evaluation is an explicit operation and is never part of normal `pytest`
execution. Configure the least-privilege banking reader and `GROQ_API_KEY`, then
run the complete tracked benchmark with:

```powershell
python -m backend.app.evaluation --model openai/gpt-oss-120b
```

Use `--case-id CASE_ID` repeatedly or `--category CATEGORY` to select a subset.
Each completed case is atomically persisted under the ignored
`evaluation/results/` directory. Resume an interrupted compatible run without
repeating completed stable case IDs:

```powershell
python -m backend.app.evaluation --model openai/gpt-oss-120b --resume evaluation/results/RUN_ID.json
```

Resume rejects changes to the model, reasoning effort, selected benchmark,
prompt/context fingerprint, statement timeout, or evaluation row limit.
Transient provider timeouts, unavailability, and rate limits stop the run before
the affected case is persisted, so resuming retries that case while retaining
all prior completed cases.
Generated SQL always passes structural validation, banking access validation,
and the hardened read-only PostgreSQL executor. Trusted project-authored
reference SQL uses the read-only executor separately. Correctness compares
normalized scalar, ordered-row, or unordered-row results and never SQL strings;
unordered comparison preserves duplicate-row multiplicity. Column aliases are
not part of result correctness, but column count and row shape are.
