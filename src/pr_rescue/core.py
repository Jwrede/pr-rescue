"""Find pull requests that need you, or that a stale bot closed."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass

GH_TIMEOUT = 120

_FIELDS = """
  ... on PullRequest {
    number title url state merged isDraft createdAt updatedAt closedAt
    reviewDecision mergeable body
    author { login }
    headRef { name }
    repository { nameWithOwner isArchived }
    timelineItems(itemTypes: [CLOSED_EVENT], last: 1) {
      nodes { ... on ClosedEvent { actor { login __typename } } }
    }
    reviews(states: APPROVED, first: 1) { totalCount }
    comments(last: 1) { nodes { author { login __typename } createdAt } }
    commits(last: 1) { nodes { commit { committedDate statusCheckRollup { state contexts(first: 100) {
      nodes {
        __typename
        ... on CheckRun { name conclusion }
        ... on StatusContext { context state description }
      }
    } } } } }
  }
"""

QUERY = f"""
query($q: String!, $cursor: String) {{
  search(query: $q, type: ISSUE, first: 25, after: $cursor) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ {_FIELDS} }}
  }}
}}
"""

BOT_LOGINS = {"github-actions", "stale", "dependabot", "renovate", "mergify", "k8s-ci-robot"}


class GitHubError(RuntimeError):
    pass


def gh_graphql(query: str, retries: int = 4, **variables: str) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is not None:
            args += ["-f", f"{key}={value}"]
    error = "gh api graphql failed"
    for attempt in range(retries):
        try:
            res = subprocess.run(args, capture_output=True, text=True, timeout=GH_TIMEOUT, check=False)
        except FileNotFoundError:
            raise GitHubError("the GitHub CLI (gh) is not installed") from None
        except subprocess.TimeoutExpired:
            error = f"gh api graphql timed out after {GH_TIMEOUT}s"
        else:
            if res.returncode == 0:
                return json.loads(res.stdout)
            error = res.stderr.strip() or error
            # GitHub answers heavy search queries with 502/503 now and then; other errors are final.
            if not any(code in error for code in ("502", "503", "504", "timeout")):
                break
        time.sleep(2 ** (attempt + 1))
    raise GitHubError(error)


def search(q: str, fetch=gh_graphql, limit: int = 500) -> list[dict]:
    nodes: list[dict] = []
    cursor = None
    while len(nodes) < limit:
        data = fetch(QUERY, q=q, cursor=cursor)["data"]["search"]
        nodes += [n for n in data["nodes"] if n]
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
    return nodes[:limit]


@dataclass
class Finding:
    repo: str
    number: int
    title: str
    url: str
    state: str  # open | closed
    waiting_on: str  # you | maintainers | nobody
    reason: str
    approved: bool
    idle_days: int

    def to_dict(self) -> dict:
        return asdict(self)


def _is_bot(actor: dict | None) -> bool:
    if not actor:
        return False
    login = actor.get("login", "")
    return actor.get("__typename") == "Bot" or login.endswith("[bot]") or login in BOT_LOGINS


# Status contexts that only say a deployment preview needs a maintainer's approval
# (Vercel, Netlify and similar for pull requests from forks). The author cannot act on them.
_APPROVAL_ONLY = re.compile(
    r"authori[sz]ation required|requires? (approval|authori[sz]ation)|awaiting approval", re.IGNORECASE
)
# Gate jobs that only report whether all other checks passed.
_AGGREGATE = re.compile(r"all[-_ ]?checks|required[-_ ]?checks|ci[-_ ]?success|all[-_ ]?green", re.IGNORECASE)
_FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"}


def check_states(rollup: dict | None) -> tuple[list[str], list[str]]:
    """Names of failed and of cancelled checks, without approval-only and aggregate checks."""
    if not rollup:
        return [], []
    failed, cancelled, aggregates = [], [], []
    for node in (rollup.get("contexts") or {}).get("nodes") or []:
        if node.get("__typename") == "StatusContext":
            name, state = node.get("context", ""), node.get("state")
            if _APPROVAL_ONLY.search(node.get("description") or ""):
                continue
        else:
            name, state = node.get("name", ""), node.get("conclusion")
        if state == "CANCELLED":
            cancelled.append(name)
        elif state in _FAILED:
            (aggregates if _AGGREGATE.search(name) else failed).append(name)
    if not failed and not cancelled and aggregates:
        failed = aggregates
    return failed, cancelled


def _days_since(stamp: str | None, now: dt.datetime) -> int:
    if not stamp:
        return 0
    when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return max(0, (now - when).days)


def _references(pr: dict) -> set[tuple[str, int]]:
    """(repo, number) pairs that a PR body points to, e.g. "#123", "replaces #123" or a full URL."""
    repo = pr["repository"]["nameWithOwner"]
    body = pr.get("body") or ""
    refs = {(repo, int(n)) for n in re.findall(r"(?<![\w/])#(\d+)", body)}
    refs |= {(f"{o}/{r}", int(n)) for o, r, n in re.findall(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)", body)}
    return refs


def classify(pr: dict, author: str, now: dt.datetime, successors: dict[tuple[str, int], str]) -> Finding | None:
    repo = pr["repository"]["nameWithOwner"]
    approved = pr.get("reviewDecision") == "APPROVED" or pr["reviews"]["totalCount"] > 0
    common = {"repo": repo, "number": pr["number"], "title": pr["title"], "url": pr["url"], "approved": approved}

    if pr["state"] == "CLOSED":
        if pr.get("merged") or pr["repository"]["isArchived"]:
            return None
        events = pr["timelineItems"]["nodes"]
        actor = events[-1].get("actor") if events else None
        idle = _days_since(pr.get("closedAt"), now)
        if not _is_bot(actor):
            return None
        successor = successors.get((repo, pr["number"]))
        if successor:
            return Finding(
                **common,
                state="closed",
                waiting_on="nobody",
                idle_days=idle,
                reason=f"closed by {actor['login']}, resubmitted as {successor}",
            )
        if pr.get("headRef") is None:
            reason = f"closed by {actor['login']}, branch deleted: open a new PR"
        else:
            reason = f"closed by {actor['login']}, branch still exists: reopen or resubmit"
        if approved:
            reason = "APPROVED, " + reason
        return Finding(**common, state="closed", waiting_on="you", idle_days=idle, reason=reason)

    idle = _days_since(pr.get("updatedAt"), now)
    commits = pr["commits"]["nodes"]
    failed, cancelled = check_states(commits[-1]["commit"].get("statusCheckRollup") if commits else None)
    last_comment = (pr["comments"]["nodes"] or [None])[-1]
    last_commit = commits[-1]["commit"]["committedDate"] if commits else pr["createdAt"]

    if pr.get("isDraft"):
        waiting, reason = "you", "draft"
    elif pr.get("mergeable") == "CONFLICTING":
        waiting, reason = "you", "merge conflict"
    elif pr.get("reviewDecision") == "CHANGES_REQUESTED":
        waiting, reason = "you", "changes requested"
    elif failed:
        waiting, reason = "you", "checks failing: " + ", ".join(failed[:3]) + (" ..." if len(failed) > 3 else "")
    elif (
        last_comment
        and last_comment["author"]
        and last_comment["author"]["login"] != author
        and not _is_bot(last_comment["author"])
        and last_comment["createdAt"] > last_commit
    ):
        waiting, reason = "you", f"unanswered comment from {last_comment['author']['login']}"
    elif cancelled:
        waiting, reason = "maintainers", "checks cancelled, need a rerun: " + ", ".join(cancelled[:3])
    elif approved:
        waiting, reason = "maintainers", "approved, not merged"
    else:
        waiting, reason = "maintainers", "waiting for review"
    return Finding(**common, state="open", waiting_on=waiting, reason=reason, idle_days=idle)


def scan(author: str, fetch=gh_graphql, now: dt.datetime | None = None, include_own: bool = False) -> list[Finding]:
    """Classify the author's open PRs and stale-bot-closed PRs in other people's repositories."""
    now = now or dt.datetime.now(dt.timezone.utc)
    scope = "" if include_own else f" -user:{author}"
    open_prs = search(f"is:pr is:open author:{author} archived:false{scope}", fetch)
    closed = search(f"is:pr is:closed is:unmerged author:{author} archived:false{scope}", fetch)
    merged = search(f"is:pr is:merged author:{author}{scope} updated:>{(now - dt.timedelta(days=365)).date()}", fetch)

    successors: dict[tuple[str, int], str] = {}
    for pr in open_prs + merged:
        for ref in _references(pr):
            successors.setdefault(ref, f"#{pr['number']}")

    findings = []
    for pr in open_prs + closed:
        f = classify(pr, author, now, successors)
        if f:
            findings.append(f)
    order = {"you": 0, "maintainers": 1, "nobody": 2}
    findings.sort(key=lambda f: (order[f.waiting_on], not f.approved, -f.idle_days))
    return findings


def current_login(fetch=gh_graphql) -> str:
    return fetch("query { viewer { login } }")["data"]["viewer"]["login"]
