#!/usr/bin/env python3
"""
fetch_github_stats.py

Queries the GitHub GraphQL API for `viewer` repository and contribution
metrics (Public + Private), then updates:

  1. README.md  - Shields.io badge URLs (via regex) and the summary
                  metric table (via HTML comment markers).
  2. stats.json - Machine-readable snapshot consumed by vinayjagtap.vercel.app.

Requires a Fine-Grained Personal Access Token with:
  - Account permissions: Read user profile data ("user" on classic PATs)
  - Repository permissions: Metadata -> Read-only
exported as GH_STATS_TOKEN. All data stays inside this repo; no
proprietary source code is ever read or published.

Usage:
  python3 scripts/fetch_github_stats.py [--dry-run]

Stdlib only - no third-party dependencies.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
STATS_JSON_PATH = REPO_ROOT / "stats.json"

GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
TOKEN_ENV_VAR = "GH_STATS_TOKEN"

# ---------------------------------------------------------------------------
# GraphQL query
# ---------------------------------------------------------------------------

QUERY = """
query ViewerStats {
  viewer {
    login
    name
    repositories(privacy: PUBLIC, ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
      totalCount
    }
    privateRepositories: repositories(privacy: PRIVATE, ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
      totalCount
    }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      restrictedContributionsCount
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Badge regex patterns (Shields.io flat-square URLs inside README.md)
# ---------------------------------------------------------------------------

BADGE_PATTERNS: dict[str, re.Pattern[str]] = {
    "public_repositories": re.compile(
        r"(img\.shields\.io/badge/Public%20Repos-)(\d+)(-)"
    ),
    "private_repositories": re.compile(
        r"(img\.shields\.io/badge/Private%20R%26D%20Repos-)(\d+)(-)"
    ),
    "total_contributions": re.compile(
        r"(img\.shields\.io/badge/Contributions-)(\d+)(-)"
    ),
}

# ---------------------------------------------------------------------------
# HTML comment markers for the summary metric table
# ---------------------------------------------------------------------------

MARKER_PATTERNS: dict[str, re.Pattern[str]] = {
    "public_repositories": re.compile(
        r"(<!-- STATS:PUBLIC_REPOS -->)\s*\d*\s*(<!-- /STATS:PUBLIC_REPOS -->)"
    ),
    "private_repositories": re.compile(
        r"(<!-- STATS:PRIVATE_REPOS -->)\s*\d*\s*(<!-- /STATS:PRIVATE_REPOS -->)"
    ),
    "total_contributions": re.compile(
        r"(<!-- STATS:TOTAL_CONTRIBUTIONS -->)\s*\d*\s*(<!-- /STATS:TOTAL_CONTRIBUTIONS -->)"
    ),
}

STATS_JSON_FIELDS = {
    "public_repositories": "public_repositories",
    "private_repositories": "private_repositories",
    "total_commit_contributions": "total_commit_contributions",
    "total_pull_request_contributions": "total_pull_request_contributions",
    "restricted_contributions_count": "restricted_contributions_count",
}


def fetch_viewer_stats(token: str) -> dict:
    """Execute the GraphQL query and return a normalized stats dict."""
    payload = json.dumps({"query": QUERY}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "vinukj-profile-stats-action",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"[error] GitHub API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"[error] Could not reach GitHub API: {exc.reason}") from exc

    if body.get("errors"):
        messages = "; ".join(err.get("message", str(err)) for err in body["errors"])
        raise SystemExit(f"[error] GraphQL errors: {messages}")

    viewer = body.get("data", {}).get("viewer")
    if not viewer:
        raise SystemExit("[error] GraphQL response contained no `viewer` data.")

    contribs = viewer.get("contributionsCollection", {})

    def as_int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return {
        "login": viewer.get("login", ""),
        "name": viewer.get("name", ""),
        "public_repositories": as_int(viewer.get("repositories", {}).get("totalCount")),
        "private_repositories": as_int(viewer.get("privateRepositories", {}).get("totalCount")),
        "total_commit_contributions": as_int(contribs.get("totalCommitContributions")),
        "total_pull_request_contributions": as_int(contribs.get("totalPullRequestContributions")),
        "restricted_contributions_count": as_int(contribs.get("restrictedContributionsCount")),
    }


def total_contributions(stats: dict) -> int:
    """Commits + PRs + restricted (private repo) contributions this year."""
    return (
        stats["total_commit_contributions"]
        + stats["total_pull_request_contributions"]
        + stats["restricted_contributions_count"]
    )


def update_readme(stats: dict) -> bool:
    """Rewrite badge URLs (regex) and table markers (HTML comments).

    Returns True when the README content changed.
    """
    readme_text = README_PATH.read_text(encoding="utf-8")
    original_text = readme_text

    total = total_contributions(stats)

    badge_values = {
        "public_repositories": str(stats["public_repositories"]),
        "private_repositories": str(stats["private_repositories"]),
        "total_contributions": str(total),
    }
    for key, pattern in BADGE_PATTERNS.items():
        readme_text = pattern.sub(
            lambda match, value=badge_values[key]: f"{match.group(1)}{value}{match.group(3)}",
            readme_text,
        )

    marker_values = {
        "public_repositories": stats["public_repositories"],
        "private_repositories": stats["private_repositories"],
        "total_contributions": total,
    }
    for key, pattern in MARKER_PATTERNS.items():
        readme_text = pattern.sub(
            lambda match, value=marker_values[key]: f"{match.group(1)}{value}{match.group(2)}",
            readme_text,
        )

    if readme_text != original_text:
        README_PATH.write_text(readme_text, encoding="utf-8")
        return True
    return False


def write_stats_json(stats: dict) -> bool:
    """Write stats.json for consumption by vinayjagtap.vercel.app.

    Returns True when the file content changed.
    """
    snapshot = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "github-graphql/viewer",
        "login": stats["login"],
        "name": stats["name"],
        **{field: stats[field] for field in STATS_JSON_FIELDS},
        "total_contributions": total_contributions(stats),
    }

    # Compare on counts only so the timestamp never triggers a diff by
    # itself; the workflow's `git diff --quiet` guard then prevents
    # noise commits when no metric actually moved.
    compare_snapshot = {key: value for key, value in snapshot.items() if key != "generated_at"}

    if STATS_JSON_PATH.exists():
        try:
            previous = json.loads(STATS_JSON_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = None
        if isinstance(previous, dict):
            previous_counts = {
                key: value for key, value in previous.items() if key != "generated_at"
            }
            if previous_counts == compare_snapshot:
                return False

    STATS_JSON_PATH.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return True


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if not token:
        print(
            f"[error] Missing environment variable `{TOKEN_ENV_VAR}`.\n"
            "        Create a Fine-Grained PAT (read: user profile + repository\n"
            "        metadata) and export it before running this script."
        )
        return 1

    stats = fetch_viewer_stats(token)

    print(
        "[stats] "
        f"public={stats['public_repositories']} "
        f"private={stats['private_repositories']} "
        f"contributions={total_contributions(stats)}"
    )

    readme_changed = update_readme(stats)
    stats_changed = write_stats_json(stats)

    if dry_run:
        print("[dry-run] No files written.")
        return 0

    print(f"[readme] {'updated' if readme_changed else 'no changes'}")
    print(f"[stats.json] {'updated' if stats_changed else 'no changes'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
