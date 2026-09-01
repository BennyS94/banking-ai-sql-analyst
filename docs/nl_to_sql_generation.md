# NL-to-SQL generation development checks

Phase 3 generates structured, schema-grounded PostgreSQL text. Generated SQL is
untrusted and is not executed in this workflow.

Normal automated tests use fake provider responses and require neither Groq
credentials nor network access:

```powershell
python -m unittest discover -s tests -v
```

To opt into the live Groq smoke cases, configure `GROQ_API_KEY` and the Phase 2
read-only database settings in the environment or an untracked `.env` file:

```text
GROQ_API_KEY=<your key>
GROQ_MODEL=openai/gpt-oss-20b
GROQ_REASONING_EFFORT=medium
BANKING_READER_USER=banking_reader
BANKING_READER_DATABASE_URL=postgresql+psycopg://banking_reader:<password>@localhost:5432/banking_ai
```

Then run:

```powershell
python -m backend.app.ai.smoke
```

The command emits one JSON object per case with the semantic status, SQL or
message, selected model, reasoning effort, latency, token usage, request ID and
finish reason where available. It does not print credentials, hidden reasoning
or database rows, and it never sends generated SQL to PostgreSQL.
