# pr-rescue

If you contribute to other people's projects, your pull requests end up in two kinds of limbo.
Some wait for you (a conflict, a failing check, a reviewer's question) without you noticing.
Others were closed by a stale bot after weeks without review, sometimes after they had already
been approved. GitHub's notifications do not separate these cases, and a bot closure looks
like any other closed PR.

`pr-rescue` lists your pull requests in repositories you do not own and sorts them by who has
to act next:

- **Waiting on you**:
  - merge conflict, changes requested, failing checks, draft, or an unanswered comment from a
    person since your last commit
  - PRs a bot closed without merging, marked `APPROVED` when they had an approval, and whether
    the branch still exists, so you know if a reopen can work
- **Waiting on maintainers**: approved but not merged, or no review yet, with the days idle
- **Already handled**: bot-closed PRs that a later PR of yours references, for example
  "Replaces #123"

PRs that a person closed, or that you closed yourself, are left out.

## Why

Stale bots close contributions that nobody looked at, and contributors rarely find out in time:

- [actions/stale#719](https://github.com/actions/stale/issues/719) "Default behavior can be
  disruptive for contributors of a repository" (17 reactions, open)
- [actions/stale#861](https://github.com/actions/stale/issues/861) "Issues should be kept active
  if reactions or backlinks" (17 reactions, open)
- requests to remove the stale bot in
  [nim-lang/Nim#15159](https://github.com/nim-lang/Nim/issues/15159) (30),
  [arvidn/libtorrent#6797](https://github.com/arvidn/libtorrent/issues/6797) (19),
  [prusa3d/Prusa-Firmware-Buddy#5133](https://github.com/prusa3d/Prusa-Firmware-Buddy/issues/5133) (10),
  [signalapp/Signal-iOS#5931](https://github.com/signalapp/Signal-iOS/issues/5931) (9)

(Reaction counts as of 2026-10-20.) Dashboards like [gh-dash](https://github.com/dlvhdr/gh-dash)
show open PRs well. I did not find a tool that finds bot-closed PRs or tells you who has to
act on each open one.

## Install

```
pipx install git+https://github.com/Jwrede/pr-rescue
```

Python 3.10+ and the [GitHub CLI](https://cli.github.com/) (`gh auth login`). It uses gh's
login and makes a few GraphQL search calls; no token setup is needed.

## Usage

```
$ pr-rescue
Waiting on you (3)
  acme/router#5059  checks failing  [open, 78d idle]
      Add retry budget to the proxy
      https://github.com/acme/router/pull/5059
  acme/sdk#1472  closed by github-actions, branch still exists: reopen or resubmit  [closed, 68d idle]
      fix(cli): pass keyword arguments to the start function
      https://github.com/acme/sdk/pull/1472
  acme/storage#9066  merge conflict  [open, 34d idle]
      feat: per-test repeat pass rate threshold
      https://github.com/acme/storage/pull/9066

Waiting on maintainers (2)
  acme/gateway#1638  approved, not merged  [open, 0d idle]
  ...

Already handled (1)
  acme/serving#5636  closed by github-actions, resubmitted as #6421  [closed, 60d idle]
```

(Example output with illustrative repository names.)

Options:

| Option | Meaning |
|---|---|
| `--author LOGIN` | someone else's public PRs (default: the account gh is logged in as) |
| `--include-own` | also include repositories you own |
| `--json` | machine-readable list with `waiting_on`, `reason`, `approved`, `idle_days` |

Exit code 2 means the GitHub query failed. Transient 502/503 answers from GitHub's search are
retried.

## Limits

- Bot detection uses the account type and a short list of well-known bot logins. A person
  closing PRs from a personal account counts as a person.
- A reopen can still fail when the base branch moved on or the head branch was force-pushed.
  Then open a new PR and mention the old one, and `pr-rescue` will list it under "Already
  handled".
- GitHub search returns at most 1000 results per query.

## License

MIT
