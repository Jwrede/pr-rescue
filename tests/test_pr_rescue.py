import datetime as dt
import json

from pr_rescue import cli
from pr_rescue.core import QUERY, classify, scan

NOW = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)


def pr(number, state="OPEN", **kw):
    base = {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/acme/lib/pull/{number}",
        "state": state,
        "merged": False,
        "isDraft": False,
        "createdAt": "2026-05-01T00:00:00Z",
        "updatedAt": "2026-09-20T00:00:00Z",
        "closedAt": None,
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeable": "MERGEABLE",
        "body": "",
        "author": {"login": "me"},
        "headRef": {"name": "fix"},
        "repository": {"nameWithOwner": "acme/lib", "isArchived": False},
        "timelineItems": {"nodes": []},
        "reviews": {"totalCount": 0},
        "comments": {"nodes": []},
        "commits": {"nodes": [{"commit": {"committedDate": "2026-05-01T00:00:00Z", "statusCheckRollup": None}}]},
    }
    base.update(kw)
    return base


def bot_closed(number, **kw):
    return pr(
        number,
        state="CLOSED",
        closedAt="2026-08-21T00:00:00Z",
        timelineItems={"nodes": [{"actor": {"login": "github-actions", "__typename": "Bot"}}]},
        **kw,
    )


def test_approved_pr_closed_by_bot_is_flagged():
    f = classify(bot_closed(5, reviewDecision="APPROVED", reviews={"totalCount": 2}), "me", NOW, {})
    assert f.waiting_on == "you" and f.approved
    assert f.reason.startswith("APPROVED, closed by github-actions, branch still exists")
    assert f.idle_days == 60


def test_deleted_branch_and_successor():
    assert "branch deleted" in classify(bot_closed(6, headRef=None), "me", NOW, {}).reason
    f = classify(bot_closed(7), "me", NOW, {("acme/lib", 7): "#9"})
    assert f.waiting_on == "nobody" and "resubmitted as #9" in f.reason


def test_closed_by_human_or_merged_is_ignored():
    human = pr(8, state="CLOSED", timelineItems={"nodes": [{"actor": {"login": "maintainer", "__typename": "User"}}]})
    assert classify(human, "me", NOW, {}) is None
    assert classify(bot_closed(9, merged=True), "me", NOW, {}) is None


def test_open_states():
    assert classify(pr(1, mergeable="CONFLICTING"), "me", NOW, {}).reason == "merge conflict"
    assert classify(pr(2, reviewDecision="CHANGES_REQUESTED"), "me", NOW, {}).reason == "changes requested"
    failing = {
        "nodes": [{"commit": {"committedDate": "2026-05-01T00:00:00Z", "statusCheckRollup": {"state": "FAILURE"}}}]
    }
    assert classify(pr(3, commits=failing), "me", NOW, {}).reason == "checks failing"
    approved = classify(pr(4, reviewDecision="APPROVED"), "me", NOW, {})
    assert (approved.waiting_on, approved.reason) == ("maintainers", "approved, not merged")
    assert classify(pr(10), "me", NOW, {}).reason == "waiting for review"
    assert classify(pr(11, isDraft=True), "me", NOW, {}).reason == "draft"


def test_unanswered_human_comment_after_last_commit():
    comment = {"nodes": [{"author": {"login": "maint", "__typename": "User"}, "createdAt": "2026-06-01T00:00:00Z"}]}
    f = classify(pr(12, comments=comment), "me", NOW, {})
    assert (f.waiting_on, f.reason) == ("you", "unanswered comment from maint")
    bot = {"nodes": [{"author": {"login": "codecov", "__typename": "Bot"}, "createdAt": "2026-06-01T00:00:00Z"}]}
    assert classify(pr(13, comments=bot), "me", NOW, {}).reason == "waiting for review"
    mine = {"nodes": [{"author": {"login": "me", "__typename": "User"}, "createdAt": "2026-06-01T00:00:00Z"}]}
    assert classify(pr(14, comments=mine), "me", NOW, {}).reason == "waiting for review"


class FakeGitHub:
    def __init__(self, pages):
        self.pages = pages
        self.queries = []

    def __call__(self, query, q=None, cursor=None):
        assert query == QUERY
        self.queries.append(q)
        key = "open" if "is:open" in q else "merged" if "is:merged" in q else "closed"
        nodes = self.pages[key]
        return {"data": {"search": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}


def test_scan_links_resubmissions_and_sorts():
    fake = FakeGitHub(
        {
            "open": [pr(20, body="Replaces #5 after the stale bot closed it."), pr(21, reviewDecision="APPROVED")],
            "closed": [bot_closed(5, reviewDecision="APPROVED"), bot_closed(6, reviewDecision="APPROVED")],
            "merged": [],
        }
    )
    findings = scan("me", fetch=fake, now=NOW)
    order = [(f.number, f.waiting_on) for f in findings]
    assert order[0] == (6, "you")
    assert (5, "nobody") in order and (21, "maintainers") in order
    assert all("-user:me" in q for q in fake.queries)


def test_cli_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "scan", lambda author, include_own=False: [])
    assert cli.main(["--author", "me", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
