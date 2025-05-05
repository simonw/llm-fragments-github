from typing import List, Tuple
import httpx
import llm
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse
import time


@llm.hookimpl
def register_fragment_loaders(register):
    register("github", github_loader)
    register("issue", github_issue_loader)


def github_loader(argument: str) -> List[llm.Fragment]:
    """
    Load files from a GitHub repository as fragments

    Argument is a GitHub repository URL or username/repository
    """
    # Normalize the repository argument
    if not argument.startswith(("http://", "https://")):
        # Assume format is username/repo
        repo_url = f"https://github.com/{argument}.git"
    else:
        repo_url = argument
        if not repo_url.endswith(".git"):
            repo_url = f"{repo_url}.git"

    # Create a temporary directory to clone the repository
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # Clone the repository with --no-checkout first
            subprocess.run(
                ["git", "clone", "--depth=1", "--filter=blob:none", repo_url, temp_dir],
                check=True,
                capture_output=True,
                text=True,
            )

            # Checkout files without .git metadata
            subprocess.run(
                ["git", "checkout", "HEAD", "--", "."],
                check=True,
                capture_output=True,
                text=True,
                cwd=temp_dir,
            )

            # Remove the .git directory if it still exists
            git_dir = pathlib.Path(temp_dir) / ".git"
            if git_dir.exists():
                shutil.rmtree(git_dir)

            # Process the cloned repository
            repo_path = pathlib.Path(temp_dir)
            fragments: List[llm.Fragment] = []

            # Walk through all files in the repository
            for file_path in repo_path.glob("**/*"):
                if file_path.is_file():
                    try:
                        # Try to read the file as UTF-8
                        content = file_path.read_text(encoding="utf-8")

                        # Create a relative path for the fragment identifier
                        relative_path = file_path.relative_to(repo_path)

                        # Add the file as a fragment
                        fragments.append(
                            llm.Fragment(content, f"{argument}/{relative_path}")
                        )
                    except UnicodeDecodeError:
                        # Skip files that can't be decoded as UTF-8
                        continue

            return fragments
        except subprocess.CalledProcessError as e:
            # Handle Git errors
            raise ValueError(f"Failed to clone repository {repo_url}: {e.stderr}")
        except Exception as e:
            # Handle other errors
            raise ValueError(f"Error processing repository {repo_url}: {str(e)}")


def github_issue_loader(argument: str) -> List[llm.Fragment]:
    """
    Fetch one or more GitHub issues (and their comments) as Markdown fragments.

    Argument can be:
      - "owner/repo/NUMBER"
      - "owner/repo/NUM1,NUM2,NUM3"
      - "https://github.com/owner/repo/issues/NUMBER"
      - "https://github.com/owner/repo/issues/NUM1,NUM2,NUM3"
    """
    owner, repo, numbers = _parse_issue_argument(argument)
    client = _github_client()

    fragments: List[llm.Fragment] = []
    for number in numbers:
        fragments.append(_load_single_issue(client, owner, repo, number))
    return fragments


def _parse_issue_argument(arg: str) -> Tuple[str, str, List[int]]:
    """
    Returns (owner, repo, [number, ...]) or raises ValueError.
    Supports comma-separated numbers.
    """
    # Form 1: full URL
    if arg.startswith("http://") or arg.startswith("https://"):
        parsed = urlparse(arg)
        parts = parsed.path.strip("/").split("/")
        # /owner/repo/issues/123 or /owner/repo/issues/1,2,3
        if len(parts) >= 4 and parts[2] == "issues":
            owner, repo = parts[0], parts[1]
            number_part = parts[3]
            nums = [int(n) for n in number_part.split(",") if n.isdigit()]
            if not nums:
                raise ValueError(f"No valid issue numbers in {arg}")
            return owner, repo, nums

    # Form 2: owner/repo/number or owner/repo/1,2,3
    m = re.match(r"([^/]+)/([^/]+)/([\d,]+)$", arg)
    if m:
        owner, repo, number_part = m.groups()
        nums = [int(n) for n in number_part.split(",") if n.isdigit()]
        if not nums:
            raise ValueError(f"No valid issue numbers in {arg}")
        return owner, repo, nums

    raise ValueError(
        "Issue fragments must be owner/repo/NUMBER(s) or a full GitHub issue URL"
    )


def _load_single_issue(
    client: httpx.Client, owner: str, repo: str, number: int
) -> llm.Fragment:
    issue_api = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"

    # 1. The issue itself
    issue_resp = _get_with_rate_limit(client, issue_api)
    _raise_for_status(issue_resp, issue_api)
    issue = issue_resp.json()

    # 2. All comments (pagination)
    comments = _get_all_pages(client, f"{issue_api}/comments?per_page=100")

    # 3. Markdown
    markdown = _to_markdown(issue, comments)

    return llm.Fragment(
        markdown, source=f"https://github.com/{owner}/{repo}/issues/{number}"
    )


def _get_with_rate_limit(client: httpx.Client, url: str) -> httpx.Response:
    """
    Perform client.get(url). If GitHub responds 403 or 429 with
    rate-limit headers, sleep until reset and retry.
    """
    while True:
        resp = client.get(url)
        if resp.status_code in (403, 429):
            # Try Retry-After first
            ra = resp.headers.get("Retry-After")
            if ra and ra.isdigit():
                wait = int(ra)
            elif "X-RateLimit-Reset" in resp.headers:
                reset_ts = int(resp.headers["X-RateLimit-Reset"])
                now = int(time.time())
                wait = max(reset_ts - now, 0)
            else:
                # fallback
                wait = 60
            time.sleep(wait)
            continue
        return resp


def _get_all_pages(client: httpx.Client, url: str) -> List[dict]:
    items: List[dict] = []
    while url:
        resp = _get_with_rate_limit(client, url)
        _raise_for_status(resp, url)
        items.extend(resp.json())

        # Link header pagination
        url = None
        link = resp.headers.get("Link")
        if link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part[part.find("<") + 1 : part.find(">")]
                    break
    return items


def _parse_single_argument(arg: str) -> Tuple[str, str, int]:
    """
    Kept for backwards compatibility if you need a single issue parser.
    """
    owner, repo, nums = _parse_issue_argument(arg)
    if len(nums) != 1:
        raise ValueError("Expected exactly one issue number")
    return owner, repo, nums[0]


def _github_client() -> httpx.Client:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(headers=headers, timeout=30.0, follow_redirects=True)


def _raise_for_status(resp: httpx.Response, url: str) -> None:
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as ex:
        raise ValueError(
            f"GitHub API request failed [{resp.status_code}] for {url}"
        ) from ex


def _to_markdown(issue: dict, comments: List[dict]) -> str:
    md: List[str] = []
    md.append(f"# {issue['title']}\n")
    md.append(f"*Posted by @{issue['user']['login']}*\n")
    if issue.get("body"):
        md.append(issue["body"] + "\n")

    if comments:
        md.append("---\n")
        for c in comments:
            md.append(f"### Comment by @{c['user']['login']}\n")
            if c.get("body"):
                md.append(c["body"] + "\n")
            md.append("---\n")

    return "\n".join(md).rstrip() + "\n"
