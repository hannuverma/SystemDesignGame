from __future__ import annotations

import argparse
import sys

from engine.config import Settings
from engine.selftest.runner import run_flaw_selftest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run automated flaw selftests (vulnerable -> fixed -> broke_business)."
    )
    parser.add_argument(
        "flaw_id",
        nargs="?",
        default=None,
        help="Optional specific flaw ID to test (default: all flaws in content/flaws).",
    )
    args = parser.parse_args()

    settings = Settings()
    flaws_dir = settings.content_dir / "flaws"
    if not flaws_dir.is_dir():
        print(f"Flaws directory not found: {flaws_dir}", file=sys.stderr)
        sys.exit(1)

    if args.flaw_id:
        target_dir = flaws_dir / args.flaw_id
        if not target_dir.is_dir():
            print(f"Flaw directory not found: {target_dir}", file=sys.stderr)
            sys.exit(1)
        flaw_dirs = [target_dir]
    else:
        flaw_dirs = [
            d for d in flaws_dir.iterdir() if d.is_dir() and (d / "flaw.yaml").is_file()
        ]

    if not flaw_dirs:
        print("No flaw modules found to test.")
        sys.exit(0)

    print(f"Running selftest for {len(flaw_dirs)} flaw(s)...")
    failed = False
    for f_dir in flaw_dirs:
        print(f"\n--- Testing flaw: {f_dir.name} ---")
        res = run_flaw_selftest(f_dir, settings=settings)
        if res.passed:
            print(
                f"[PASS] {res.flaw_id}: baseline={res.baseline_verdict.status if res.baseline_verdict else 'N/A'}, "
                f"ref_fix={res.ref_fix_verdict.status if res.ref_fix_verdict else 'N/A'}, "
                f"broken_fix={res.broken_fix_verdict.status if res.broken_fix_verdict else 'N/A'}"
            )
        else:
            print(f"[FAIL] {res.flaw_id}: {res.error_message}")
            failed = True

    if failed:
        sys.exit(1)
    else:
        print("\nAll flaw selftests passed successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()
