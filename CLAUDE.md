# AI FDE Assignment 1: Harbourline Support Intake

Week 1 assignment "ship the intake": one support email in, one schema-valid
`Intake` out. See README.md for setup and design, and engagement/solution-brief.md
for the client brief.

## Grading checklist (done or not done)
- One documented command (`python scripts/run_samples.py`) produces five schema-valid objects.
- Priya's email (`01_priya_coverage`) yields `next_action: ask` with `policy_number` in `missing_fields`.
- A validation failure is logged, not dropped (`--inject-failure` demonstrates it).
- The loop stops at a valid object or at 3 model calls (`MAX_CALLS` in src/intake.py).
- The brief names at least three exclusions, including sending email and paying a claim.
- Submit: push, then `git tag week-1` and `git push origin week-1`.

## Constraints from the course
- `prompts/intake_v1.md` is the only prompt. Make a new version as a new file, not by editing v1 after submission.
- Config (model, max tokens) comes from the environment.
- No retrieval, tickets or web UI this week.
- Synthetic data only.

## Running it (Windows)
Use the project venv at `.venv`, never Anaconda's base Python:
- `.\.venv\Scripts\python.exe -m pytest -q`
- `.\.venv\Scripts\python.exe scripts\run_samples.py --inject-failure 02_billing_double_charge`
- Recreate: `py -3.12 -m venv .venv`, then `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.

`python` on PATH is Anaconda base (numpy 1.24, pandas 2.0, pydantic 1.10). Installing
requirements there once broke it, and it had to be rolled back using `anaconda3\conda-meta`.

## Model provider
- No Anthropic credits yet, so `.env` uses Google Gemini's free tier through the
  OpenAI-compatible endpoint (`LLM_PROVIDER=openai_compatible`, `LLM_MODEL=gemini-3.8-flash`).
- `gemini-2.5-flash` returns 404 for new keys. Free-tier calls take about 100 s each and
  sometimes return 503 when the model is busy.
- Installing local models (Ollama) is blocked on this laptop; don't retry it.
- To switch to Claude: `LLM_PROVIDER=anthropic`, set `ANTHROPIC_API_KEY`, `LLM_MODEL=claude-haiku-5-5`.

## Notes
- `_to_delete/` (gitignored) holds the earlier cardiology-clinic version and the original zip.
