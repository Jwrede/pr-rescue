from __future__ import annotations

import argparse
import json
import sys

from pr_rescue import __version__
from pr_rescue.core import GitHubError, current_login, scan

HEADINGS = {
    "you": "Waiting on you",
    "maintainers": "Waiting on maintainers",
    "nobody": "Already handled",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="pr-rescue",
        description="List your pull requests in other people's repositories by who has to act next, "
        "and find the ones a stale bot closed.",
    )
    ap.add_argument("--author", help="GitHub login (default: the account gh is logged in as)")
    ap.add_argument("--include-own", action="store_true", help="also include repositories you own")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

    try:
        author = args.author or current_login()
        findings = scan(author, include_own=args.include_own)
    except GitHubError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        json.dump([f.to_dict() for f in findings], sys.stdout, indent=2)
        print()
        return 0

    for key, heading in HEADINGS.items():
        group = [f for f in findings if f.waiting_on == key]
        if not group:
            continue
        print(f"{heading} ({len(group)})")
        for f in group:
            print(f"  {f.repo}#{f.number}  {f.reason}  [{f.state}, {f.idle_days}d idle]")
            print(f"      {f.title[:90]}")
            print(f"      {f.url}")
        print()
    if not findings:
        print(f"Nothing open and no stale-bot closures for {author}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
