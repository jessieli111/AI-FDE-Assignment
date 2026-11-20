"""Run every sample email through the intake.

  python scripts/run_samples.py
  python scripts/run_samples.py --inject-failure 02_billing_double_charge

Prints one JSON object per email to stdout (the Intake plus calls, tokens and
latency). Writes, under runs/week-1/:
  <email>.json         the validated Intake (only when valid)
  <email>.failed.json  the errors and raw model output (only when all calls failed)
  log.jsonl            one line per model call, then one result line per email

--inject-failure corrupts the first response for that email, so the log shows
a validation error being sent back to the model and the retry.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.intake import Config, load_env, run_traced  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--emails", type=Path, default=ROOT / "data" / "emails")
    p.add_argument("--out", type=Path, default=ROOT / "runs" / "week-1")
    p.add_argument("--inject-failure", metavar="EMAIL", help="File stem whose first response is corrupted.")
    p.add_argument("--only", metavar="EMAIL", help="Run just this file stem (for debugging).")
    args = p.parse_args()

    load_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    config = Config.from_env()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    paths = sorted(args.emails.glob("*.txt"))
    if args.only:
        paths = [p for p in paths if p.stem == args.only]
    if not paths:
        print(f"No .txt emails in {args.emails}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    failures = 0
    with open(args.out / "log.jsonl", "w", encoding="utf-8") as log_file:

        def write_log(**entry) -> None:
            log_file.write(json.dumps({"run_id": run_id, **entry}, ensure_ascii=False) + "\n")

        for path in paths:
            name = path.stem
            print(f"--- {name}", file=sys.stderr)
            try:
                result = run_traced(
                    path.read_text(encoding="utf-8"),
                    config=config,
                    inject_failure=(name == args.inject_failure),
                )
            except Exception as exc:  # API or network error: record it and move on
                failures += 1
                detail = f"{type(exc).__name__}: {getattr(exc, 'body', None) or exc}"
                print(f"ERROR {detail}", file=sys.stderr)
                write_log(event="result", email=name, outcome="error", error=detail)
                print(json.dumps({"email": name, "outcome": "error", "intake": None, "error": detail}))
                continue

            for a in result.attempts:
                entry = asdict(a)
                if a.outcome == "valid":
                    entry.pop("raw")  # the valid object is in <email>.json
                write_log(
                    event="call",
                    email=name,
                    provider=result.provider,
                    model=result.model,
                    prompt_version=result.prompt_version,
                    **entry,
                )
            write_log(
                event="result",
                email=name,
                outcome=result.outcome,
                calls=len(result.attempts),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
            )

            if result.intake is not None:
                (args.out / f"{name}.json").write_text(
                    result.intake.model_dump_json(indent=2) + "\n", encoding="utf-8"
                )
            else:
                failures += 1
                (args.out / f"{name}.failed.json").write_text(
                    json.dumps([asdict(a) for a in result.attempts], indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

            print(
                json.dumps(
                    {
                        "email": name,
                        "outcome": result.outcome,
                        "intake": result.intake.model_dump() if result.intake else None,
                        "calls": len(result.attempts),
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "latency_ms": result.latency_ms,
                    },
                    ensure_ascii=False,
                )
            )

    print(f"\n{len(paths) - failures}/{len(paths)} valid. Log: {args.out / 'log.jsonl'}", file=sys.stderr)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
