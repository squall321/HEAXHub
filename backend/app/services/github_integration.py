"""GitHub integration for change-request publishing.

Uses PyGithub. PyGithub is imported lazily so the rest of the service layer
stays importable even when the dependency hasn't been installed yet
(development environments). All public callables raise informative errors
when the dependency or bot token is missing.

Hard rules:
- Never auto-merge a PR.
- Never echo the bot token back to API consumers.
- Each required_file path is double-checked against the .portal/ allow-list
  in change_request.create_draft / .issue before we get here, but we also
  bail out if any path tries to escape during publish.
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


_SSH_GIT_RE = re.compile(r"^git@(?P<host>[^:]+):(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?$")


def parse_github_url(url: str) -> tuple[str, str]:
    """Return (owner, repo_name).

    Accepts:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    """
    if not url:
        raise ValueError("GitHub URL is empty")
    url = url.strip()

    m = _SSH_GIT_RE.match(url)
    if m:
        return m.group("owner"), m.group("repo")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported scheme '{parsed.scheme}' in GitHub URL")
    parts = [p for p in (parsed.path or "").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"Cannot extract owner/repo from URL: {url}")
    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def verify_github_token(bot_token: str) -> dict[str, Any]:
    """GET /user with the token; return {login, scopes}. Used by /admin/integrations.

    Never returns the token itself.
    """
    if not bot_token:
        raise RuntimeError("GITHUB_BOT_TOKEN not configured")

    import httpx

    resp = httpx.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"GitHub token verification failed: {resp.status_code} {resp.text[:200]}"
        )
    scopes_header = resp.headers.get("X-OAuth-Scopes") or resp.headers.get("x-oauth-scopes") or ""
    scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
    data = resp.json()
    return {"login": data.get("login"), "scopes": scopes}


# ---------------------------------------------------------------------------
# PR / Issue publishing
# ---------------------------------------------------------------------------


def publish_pr(
    *,
    repo_url: str,
    bot_token: str,
    branch_name: str,
    files: list[dict[str, Any]],
    pr_title: str,
    pr_body: str,
) -> str:
    """Fork → create branch → commit files → open PR. Returns the PR URL.

    Each file dict: {"path": ".portal/...", "content": "...", "mode": "0755"?}
    """
    if not bot_token:
        raise RuntimeError("GITHUB_BOT_TOKEN not configured")
    _enforce_portal_only_paths(files)

    github = _import_github()
    gh = github.Github(bot_token)
    owner, repo_name = parse_github_url(repo_url)

    upstream = gh.get_repo(f"{owner}/{repo_name}")

    # Fork into the bot's account. If a fork already exists the API returns it.
    fork = upstream.create_fork()
    _wait_for_fork_ready(fork, github)

    default_branch = fork.default_branch
    base_sha = fork.get_branch(default_branch).commit.sha

    # If the branch already exists from a previous attempt, append a suffix.
    branch_ref = f"refs/heads/{branch_name}"
    try:
        fork.create_git_ref(ref=branch_ref, sha=base_sha)
    except Exception:  # GithubException — branch exists, etc.
        branch_name = f"{branch_name}-{int(time.time())}"
        branch_ref = f"refs/heads/{branch_name}"
        fork.create_git_ref(ref=branch_ref, sha=base_sha)

    for f in files or []:
        path = f["path"]
        content = f.get("content", "")
        fork.create_file(
            path=path,
            message=f"chore(portal): add {path}",
            content=content,
            branch=branch_name,
        )

    pr = upstream.create_pull(
        title=pr_title,
        body=pr_body,
        head=f"{fork.owner.login}:{branch_name}",
        base=upstream.default_branch,
        maintainer_can_modify=True,
    )
    return pr.html_url


def publish_issue(
    *,
    repo_url: str,
    bot_token: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> str:
    """Open an issue on the upstream repo (fallback when PR is not allowed)."""
    if not bot_token:
        raise RuntimeError("GITHUB_BOT_TOKEN not configured")

    github = _import_github()
    gh = github.Github(bot_token)
    owner, repo_name = parse_github_url(repo_url)
    repo = gh.get_repo(f"{owner}/{repo_name}")
    issue = repo.create_issue(title=title, body=body, labels=labels or [])
    return issue.html_url


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _enforce_portal_only_paths(files: list[dict[str, Any]]) -> None:
    for f in files or []:
        path = (f or {}).get("path") or ""
        if not path.startswith(".portal/"):
            raise RuntimeError(
                f"Refusing to commit '{path}' — only .portal/ files are allowed"
            )


def _import_github() -> Any:
    try:
        import github  # PyGithub
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "PyGithub is not installed. Add 'PyGithub' to backend/pyproject.toml"
            " dependencies."
        ) from exc
    return github


def _wait_for_fork_ready(fork: Any, github_mod: Any, *, max_attempts: int = 20, sleep_seconds: float = 1.5) -> None:
    """Polls until the fork's default branch is accessible. GitHub fork creation
    is asynchronous; immediately listing the branch can 404 for a moment."""
    for attempt in range(max_attempts):
        try:
            fork.get_branch(fork.default_branch)
            return
        except Exception:
            if attempt == max_attempts - 1:
                raise
            time.sleep(sleep_seconds)
