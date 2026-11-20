# Harbourline Support Intake

AI FDE course, week 1: **ship the intake.** One customer email in, one
schema-valid object out: `category`, `missing_fields`, `next_action`,
`customer_message`, `confidence`. All emails are synthetic.

> **Note on the model:** I didn't have Anthropic API credits this week, so the
> committed run in `runs/week-1/` uses Google Gemini (`gemini-3.8-flash`, free
> tier) through its OpenAI-compatible API. The intake supports both providers.
> Switching to Claude is a `.env` change only (`LLM_PROVIDER=anthropic`,
> `ANTHROPIC_API_KEY`, `LLM_MODEL=claude-haiku-5-5`), with the same prompt,
> schema, validation loop and logging. I'll rerun on Claude once I have credits.

```
email ─> prompts/intake_v1.md + record_intake tool ─> model ─> validate in code (Pydantic)
              ▲                                                          │
              └──── invalid: error text sent back (max 3 calls) ◄────────┘
                                                                         │ valid
                                                    runs/week-1/<email>.json + log.jsonl
```

## Setup from a clean checkout

Needs Python 3.10 or newer and one API key: a free Google AI Studio key
(aistudio.google.com, used for the committed run) or an Anthropic key.

```bash
git clone <repo-url> && cd <repo>
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# then paste your key into .env (LLM_API_KEY for Gemini, or see Config below for Claude)
```

## The one command

```bash
python scripts/run_samples.py
```

It reads the five emails in `data/emails/*.txt` and prints one JSON object per
email (the intake, number of calls, tokens and latency). It writes the results
to `runs/week-1/`.

To see the validation-failure path, corrupt the first response for one email:

```bash
python scripts/run_samples.py --inject-failure 02_billing_double_charge
```

The committed `runs/week-1/` was produced this way. In `log.jsonl` you'll see
call 1 `invalid` with the error text and raw output, then call 2 `valid`.

Offline tests (no key needed): `pytest -q`

## Layout

| Path | What it is |
|---|---|
| `src/intake.py` | `Intake` schema and `run(email) -> Intake`: one tool, validation in code, loop capped at 3 calls |
| `prompts/intake_v1.md` | The only prompt. Change it by adding `intake_v2.md`, so versions can be diffed. |
| `scripts/run_samples.py` | Runs `data/emails/*.txt` and writes `runs/week-1/` |
| `data/emails/` | Five synthetic sample emails |
| `runs/week-1/` | One `<email>.json` per sample and `log.jsonl` (one line per model call and per email) |
| `evals/cases.jsonl` | Empty; the samples become eval cases in week 4 |
| `engagement/solution-brief.md` | Page 1 of the client brief |
| `tests/test_intake.py` | Offline tests with a fake client |

## Config (environment or `.env`)

| Variable | Default | |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, or `openai_compatible` for Gemini, Groq, OpenRouter and others |
| `LLM_MODEL` | `claude-haiku-5-5` for anthropic | Required for `openai_compatible`; the committed run used `gemini-3.8-flash` |
| `LLM_BASE_URL` | none | For `openai_compatible`, e.g. `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `LLM_API_KEY` | none | Key for `openai_compatible` |
| `ANTHROPIC_API_KEY` | none | Key for `anthropic` |
| `MAX_TOKENS` | `1024` | Per call |

Switching provider is a `.env` change only. Each API format has a small backend
class in `src/intake.py`; the loop and validation are shared.

## Design notes

- **One tool, validated in code.** The model must call `record_intake`, whose
  input schema is `Intake`. The tool's schema isn't strict, so a wrong value
  (or broken JSON) can come back. Pydantic catches it, and the error goes back
  as the tool's result (`is_error` for Claude, a `tool` message for
  OpenAI-compatible APIs) for the model to fix.
- **A cross-field rule** that a JSON schema alone can't express: `ask` needs at
  least one missing field, and `draft` needs none.
- **Nothing is dropped.** An invalid response's raw output is stored in the log
  line for that call. If all 3 calls fail, `<email>.failed.json` keeps every
  attempt.
- **Drafts only.** `customer_message` is never sent. The prompt forbids
  confirming coverage, deciding claims, or promising payments.
