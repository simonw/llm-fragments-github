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
    Works with both github.com and GitHub Enterprise instances
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
    Works with both github.com and GitHub Enterprise instances
    """
    try:
        owner, repo, number = _parse_argument(argument)
    except ValueError as ex:
        raise ValueError(
            "Fragment must be issue:owner/repo/NUMBER or a full "
            "GitHub issue URL – received {!r}".format(argument)
        ) from ex

    # Determine if this is github.com or GitHub Enterprise
    domain = "github.com"
    if argument.startswith(("http://", "https://")):
        domain = _get_github_domain(argument)

    # Get the appropriate API base URL
    api_base = _get_api_base_url(domain)
    client = _github_client()

    issue_api = f"{api_base}/repos/{owner}/{repo}/{noun}/{number}"

    # 1. The issue itself
    issue_resp = client.get(issue_api)
    _raise_for_status(issue_resp, issue_api)
    issue = issue_resp.json()

    # 2. All comments (pagination)
    comments = _get_all_pages(client, f"{issue_api}/comments?per_page=100")

    # 3. Markdown
    raw_md = _to_markdown(issue, comments)

    # 4. Expand any blob URLs into inline code
    markdown = _expand_code_references(raw_md, client)

    url_noun = "issues" if noun == "issues" else "pull"

    # Use the appropriate domain for the source URL
    source_domain = "github.com"
    if argument.startswith(("http://", "https://")):
        source_domain = _get_github_domain(argument)

    return llm.Fragment(
        markdown,
        source=f"https://{source_domain}/{owner}/{repo}/{url_noun}/{number}",
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

    # Determine if this is github.com or GitHub Enterprise
    domain = "github.com"
    if argument.startswith(("http://", "https://")):
        domain = _get_github_domain(argument)

    # Get the appropriate API base URL
    api_base = _get_api_base_url(domain)

    client = _github_client()
    markdown_fragment = github_issue_loader(argument, noun="pulls")
    diff_api = f"{api_base}/repos/{owner}/{repo}/pulls/{number}.diff"
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


def _get_github_domain(url: str) -> str:
    """
    Extract the GitHub domain from a URL.
    Returns "github.com" for public GitHub or custom domain for GitHub Enterprise.
    """
    if url.startswith(("http://", "https://")):
        parsed = urlparse(url)
        return parsed.netloc
    return "github.com"


def _get_api_base_url(domain: str) -> str:
    """
    Returns the base API URL for the given GitHub domain.
    For github.com, returns api.github.com
    For GitHub Enterprise, returns domain/api/v3
    """
    if domain == "github.com":
        return "https://api.github.com"
    return f"https://{domain}/api/v3"


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
    current_url: str = url
    while current_url:
        resp = client.get(current_url)
        _raise_for_status(resp, current_url)
        items.extend(resp.json())

        # Link header pagination
        next_url: str | None = None
        link = resp.headers.get("Link")
        if link:
            for part in link.split(","):
                if part.endswith('rel="next"'):
                    next_url = part[part.find("<") + 1 : part.find(">")]
                    break
        current_url = next_url if next_url else ""
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


def _expand_code_references(markdown: str, client: httpx.Client) -> str:
    """
    Find GitHub blob URLs with #L… or #L…-L… in the markdown,
    fetch the file (via API when GITHUB_TOKEN is set, else raw.githubusercontent),
    extract the requested lines, and replace the URL with a fenced code block.
    """
    raw_cache: dict = {}

    blob_rx = re.compile(
        r"(https://(?P<domain>[^/]+)/(?P<owner>[^/]+)"
        r"/(?P<repo>[^/]+)/blob/"
        r"(?P<ref>[^/]+)/(?P<path>[^#\s]+)"
        r"#L(?P<start>\d+)(?:-L(?P<end>\d+))?)"
    )

    def fetch_snippet(match: re.Match) -> str:
        full_url = match.group(1)
        domain = match.group("domain")
        owner = match.group("owner")
        repo = match.group("repo")
        ref = match.group("ref")
        path = match.group("path")
        start = int(match.group("start"))
        end = int(match.group("end")) if match.group("end") else start

        token = os.getenv("GITHUB_TOKEN")
        api_base = _get_api_base_url(domain)

        if token:
            # Use GitHub Contents API with raw accept header
            fetch_url = (
                f"{api_base}/repos/{owner}/{repo}"
                f"/contents/{path}?ref={ref}"
            )
            headers = {"Accept": "application/vnd.github.v3.raw"}
        else:
            if domain == "github.com":
                fetch_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
            else:
                # GitHub Enterprise raw URL format
                fetch_url = f"https://{domain}/raw/{owner}/{repo}/{ref}/{path}"
            headers = {}

        if fetch_url not in raw_cache:
            resp = client.get(fetch_url, headers=headers)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                raw_cache[fetch_url] = None
            else:
                raw_cache[fetch_url] = resp.text.splitlines()

        lines = raw_cache.get(fetch_url)
        if not lines:
            return full_url

        end = min(end, len(lines))
        snippet = "\n".join(lines[start - 1 : end])
        ext = pathlib.Path(path).suffix.lstrip(".")
        return f"\n```{ext}\n{snippet}\n```"

    return blob_rx.sub(lambda m: fetch_snippet(m), markdown)
