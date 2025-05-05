from typing import List, Tuple
import httpx
import llm
import os
import pathlib
import re
import subprocess
import tempfile
from urllib.parse import urlparse


@llm.hookimpl
def register_fragment_loaders(register):
    register("github", github_loader)
    register("issue", github_issue_loader)
    register("pr", github_pr_loader)


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

            # Process the cloned repository
            repo_path = pathlib.Path(temp_dir)
            fragments = []

            # Walk through all files in the repository, excluding .git directory
            for root, dirs, files in os.walk(repo_path):
                # Remove .git from dirs to prevent descending into it
                if ".git" in dirs:
                    dirs.remove(".git")

                # Process files
                for file in files:
                    file_path = pathlib.Path(root) / file
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


def github_issue_loader(argument: str, noun="issues") -> llm.Fragment:
    """
    Fetch GitHub issue/pull and comments as Markdown

    Argument is either "owner/repo/NUMBER" or URL to an issue
    """
    try:
        owner, repo, number = _parse_argument(argument)
    except ValueError as ex:
        raise ValueError(
            "Fragment must be issue:owner/repo/NUMBER or a full "
            "GitHub issue URL – received {!r}".format(argument)
        ) from ex

    client = _github_client()

    issue_api = f"https://api.github.com/repos/{owner}/{repo}/{noun}/{number}"

    # 1. The issue itself
    issue_resp = client.get(issue_api)
    _raise_for_status(issue_resp, issue_api)
    issue = issue_resp.json()

    # 2. All comments (pagination)
    comments = _get_all_pages(client, f"{issue_api}/comments?per_page=100")

    # 3. Markdown
    markdown = _to_markdown(issue, comments)

    url_noun = "issues" if noun == "issues" else "pull"

    return llm.Fragment(
        markdown,
        source=f"https://github.com/{owner}/{repo}/{url_noun}/{number}",
    )


def github_pr_loader(argument: str) -> List[llm.Fragment]:
    """
    Fetch GitHub pull request with comments and diff as Markdown

    Argument is either "owner/repo/NUMBER" or URL to a pull request
    """
    try:
        owner, repo, number = _parse_argument(argument)
    except ValueError as ex:
        raise ValueError(
            "Fragment must be owner/repo/NUMBER or a full "
            "GitHub pull request URL – received {!r}".format(argument)
        ) from ex

    client = _github_client()
    markdown_fragment = github_issue_loader(argument, noun="pulls")
    diff_api = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}.diff"
    diff_resp = client.get(
        diff_api, headers={"Accept": "application/vnd.github.v3.diff"}
    )
    _raise_for_status(diff_resp, diff_api)
    diff = diff_resp.text
    return [
        markdown_fragment,
        llm.Fragment(
            diff,
            source=diff_api,
        ),
    ]


def _parse_argument(arg: str) -> Tuple[str, str, int]:
    """
    Returns (owner, repo, number) or raises ValueError
    """
    # Form 1: full URL
    if arg.startswith("http://") or arg.startswith("https://"):
        parsed = urlparse(arg)
        parts = parsed.path.strip("/").split("/")
        # /owner/repo/issues/123
        if len(parts) >= 4 and parts[2] in ("issues", "pull"):
            owner, repo, _, number = parts[:4]
            return owner, repo, int(number)

    # Form 2: owner/repo/number
    m = re.match(r"([^/]+)/([^/]+)/(\d+)$", arg)
    if m:
        owner, repo, number = m.groups()
        return owner, repo, int(number)

    raise ValueError("Issue should be org/repo/NUMBER or a full GitHub URL")


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


def _get_all_pages(client: httpx.Client, url: str) -> List[dict]:
    items: List[dict] = []
    while url:
        resp = client.get(url)
        _raise_for_status(resp, url)
        items.extend(resp.json())

        # Link header pagination
        url = None
        link = resp.headers.get("Link")
        if link:
            for part in link.split(","):
                if part.endswith('rel="next"'):
                    url = part[part.find("<") + 1 : part.find(">")]
                    break
    return items


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
