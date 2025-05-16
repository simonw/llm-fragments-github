from llm_fragments_github import github_loader, github_issue_loader, github_pr_loader
import pytest


def test_github_loader():
    fragments = github_loader("simonw/test-repo-for-llm-fragments-github")
    normalized = [
        (fragment.source.replace("\\", "/"), str(fragment)) for fragment in fragments
    ]
    assert normalized == [
        (
            "simonw/test-repo-for-llm-fragments-github/README.md",
            "# test-repo-for-llm-fragments-github\nUsed by tests for https://github.com/simonw/llm-fragments-github\n",
        ),
        (
            "simonw/test-repo-for-llm-fragments-github/example/file.txt",
            "This is an example file.\n",
        ),
    ]


@pytest.mark.parametrize(
    "argument",
    (
        "simonw/test-repo-for-llm-fragments-github/1",
        "https://github.com/simonw/test-repo-for-llm-fragments-github/issues/1",
    ),
)
def test_github_issue_loader(argument):
    fragment = github_issue_loader(argument)
    assert (
        fragment.source
        == "https://github.com/simonw/test-repo-for-llm-fragments-github/issues/1"
    )
    assert str(fragment) == (
        "# Example issue\n\n"
        "*Posted by @simonw*\n\n"
        "Has a description.\n\n"
        "---\n\n"
        "### Comment by @simonw\n\n"
        "Comment 1.\n\n"
        "---\n\n### Comment by @simonw\n\n"
        "Comment 2.\n\n"
        "---\n"
    )
    # Test errors
    with pytest.raises(ValueError) as ex1:
        github_issue_loader("simonw/test-repo-for-llm-fragments-github/1234")
    assert (
        str(ex1.value)
        == "GitHub API request failed [404] for https://api.github.com/repos/simonw/test-repo-for-llm-fragments-github/issues/1234"
    )
    with pytest.raises(ValueError) as ex2:
        github_issue_loader("This is bad")
    assert (
        str(ex2.value)
        == "Fragment must be issue:owner/repo/NUMBER or a full GitHub issue URL – received 'This is bad'"
    )


@pytest.mark.parametrize(
    "argument",
    (
        "simonw/test-repo-for-llm-fragments-github/2",
        "https://github.com/simonw/test-repo-for-llm-fragments-github/pull/2",
    ),
)
def test_github_pr_loader(argument):
    fragments = github_pr_loader(argument)
    assert len(fragments) == 2
    assert (
        fragments[0].source
        == "https://github.com/simonw/test-repo-for-llm-fragments-github/pull/2"
    )
    assert (
        str(fragments[0])
        == "# Example PR\n\n*Posted by @simonw*\n\nThis is an example PR.\n"
    )
    assert (
        fragments[1].source
        == "https://api.github.com/repos/simonw/test-repo-for-llm-fragments-github/pulls/2.diff"
    )
    assert str(fragments[1]) == (
        "diff --git a/example/file.txt b/example/file.txt\n"
        "index e738d76..daf57b5 100644\n"
        "--- a/example/file.txt\n"
        "+++ b/example/file.txt\n"
        "@@ -1 +1,3 @@\n"
        " This is an example file.\n"
        "+\n"
        "+It has been modified in this PR.\n"
        "\\ No newline at end of file\n"
    )


def test_github_issue_with_code_references(httpx_mock, monkeypatch):
    # Ensure we hit the raw.githubusercontent.com branch
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    owner = "foo"
    repo = "bar"
    number = 1

    # 1) Mock the issue payload
    issue_api = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    issue_body = (
        "Here is some context.\n\n"
        "Check out this snippet:\n\n"
        "https://github.com/foo/bar/blob/main/file.py#L2-L3\n"
    )
    httpx_mock.add_response(
        method="GET",
        url=issue_api,
        json={
            "title": "Test Issue",
            "user": {"login": "alice"},
            "body": issue_body,
        },
    )

    # 2) Mock the comments (none)
    comments_api = f"{issue_api}/comments?per_page=100"
    httpx_mock.add_response(method="GET", url=comments_api, json=[])

    # 3) Mock fetching the raw file from raw.githubusercontent.com
    raw_url = "https://raw.githubusercontent.com/foo/bar/main/file.py"
    file_contents = "line1\nline2\nline3\n"
    httpx_mock.add_response(method="GET", url=raw_url, text=file_contents)

    # Load the fragment
    fragment = github_issue_loader(f"{owner}/{repo}/{number}")
    text = str(fragment)

    # It should have inlined lines 2–3 in a ```py fence
    assert "```py\nline2\nline3\n```" in text

    # And it should no longer contain the original blob URL
    assert "github.com/foo/bar/blob/main/file.py" not in text

    # Also check the header and poster line remain intact
    assert text.startswith("# Test Issue")
    assert "*Posted by @alice*" in text


def test_github_enterprise_issue_loader(httpx_mock, monkeypatch):
    # Test with a GitHub Enterprise URL
    enterprise_domain = "git.example.org"
    owner = "engineering"
    repo = "project"
    number = 5

    # Set up mock responses for GitHub Enterprise APIs
    enterprise_api_base = f"https://{enterprise_domain}/api/v3"
    issue_api = f"{enterprise_api_base}/repos/{owner}/{repo}/issues/{number}"
    issue_url = f"https://{enterprise_domain}/{owner}/{repo}/issues/{number}"

    # Mock the issue response
    httpx_mock.add_response(
        method="GET",
        url=issue_api,
        json={
            "title": "Enterprise Issue",
            "user": {"login": "developer"},
            "body": "This is an issue on a private GitHub Enterprise instance.",
        },
    )

    # Mock the comments response
    comments_api = f"{issue_api}/comments?per_page=100"
    httpx_mock.add_response(
        method="GET",
        url=comments_api,
        json=[
            {
                "user": {"login": "manager"},
                "body": "This needs to be fixed soon.",
            }
        ],
    )

    # Load the fragment using the enterprise URL
    fragment = github_issue_loader(issue_url)

    # Verify the source URL is correctly preserved with enterprise domain
    assert fragment.source == issue_url

    # Verify content includes the issue details
    content = str(fragment)
    assert "# Enterprise Issue" in content
    assert "*Posted by @developer*" in content
    assert "This is an issue on a private GitHub Enterprise instance." in content
    assert "### Comment by @manager" in content
    assert "This needs to be fixed soon." in content


def test_github_enterprise_pr_loader(httpx_mock):
    # Test with a GitHub Enterprise pull request URL
    enterprise_domain = "git.company.com"
    owner = "devteam"
    repo = "application"
    number = 42

    # Set up mock responses for GitHub Enterprise APIs
    enterprise_api_base = f"https://{enterprise_domain}/api/v3"
    pr_url = f"https://{enterprise_domain}/{owner}/{repo}/pull/{number}"

    # Mock the PR API (using the same structure as issues but with 'pulls' endpoint)
    pr_api = f"{enterprise_api_base}/repos/{owner}/{repo}/pulls/{number}"
    httpx_mock.add_response(
        method="GET",
        url=pr_api,
        json={
            "title": "Enterprise PR",
            "user": {"login": "engineer"},
            "body": "This is a pull request on a GitHub Enterprise instance.",
        },
    )

    # Mock the PR comments response
    comments_api = f"{pr_api}/comments?per_page=100"
    httpx_mock.add_response(method="GET", url=comments_api, json=[])

    # Mock the PR diff response
    diff_api = f"{enterprise_api_base}/repos/{owner}/{repo}/pulls/{number}.diff"
    diff_content = (
        "diff --git a/src/main.js b/src/main.js\n"
        "index abc123..def456 100644\n"
        "--- a/src/main.js\n"
        "+++ b/src/main.js\n"
        "@@ -10,6 +10,8 @@\n"
        " function initialize() {\n"
        "   console.log('Starting application');\n"
        "+  // Add new initialization code\n"
        "+  setupFeature();\n"
        " }\n"
    )
    httpx_mock.add_response(
        method="GET",
        url=diff_api,
        headers={"Accept": "application/vnd.github.v3.diff"},
        text=diff_content,
    )

    # Load the PR fragments
    fragments = github_pr_loader(pr_url)

    # Verify we have two fragments (PR markdown and diff)
    assert len(fragments) == 2

    # Verify the source URLs use the enterprise domain
    assert fragments[0].source == pr_url
    assert fragments[1].source == diff_api

    # Verify PR content
    assert "# Enterprise PR" in str(fragments[0])
    assert "*Posted by @engineer*" in str(fragments[0])

    # Verify diff content
    assert "diff --git a/src/main.js b/src/main.js" in str(fragments[1])
    assert "+  setupFeature();" in str(fragments[1])


def test_github_enterprise_code_references(httpx_mock, monkeypatch):
    # Test code references expansion with GitHub Enterprise URLs
    enterprise_domain = "github.internal.corp"
    owner = "core"
    repo = "service"
    number = 15

    # Clear GitHub token to test the raw URL path
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # Set up mock responses
    enterprise_api_base = f"https://{enterprise_domain}/api/v3"
    issue_api = f"{enterprise_api_base}/repos/{owner}/{repo}/issues/{number}"

    # Issue with a code reference to a blob on the enterprise GitHub
    issue_body = (
        "Please review this code:\n\n"
        f"https://{enterprise_domain}/{owner}/{repo}/blob/main/src/utils.ts#L25-L30\n"
    )

    httpx_mock.add_response(
        method="GET",
        url=issue_api,
        json={
            "title": "Code Review Needed",
            "user": {"login": "reviewer"},
            "body": issue_body,
        },
    )

    # Mock comments response
    comments_api = f"{issue_api}/comments?per_page=100"
    httpx_mock.add_response(method="GET", url=comments_api, json=[])

    # Mock the raw file content from the enterprise instance
    # The raw URL format for GitHub Enterprise is different from github.com
    raw_url = f"https://{enterprise_domain}/raw/{owner}/{repo}/main/src/utils.ts"
    file_content = (
        "// Utility functions\n" * 24 +  # Lines 1-24
        "export function validateInput(data: any): boolean {\n"  # Line 25
        "  if (!data) return false;\n"
        "  \n"
        "  if (typeof data !== 'object') return false;\n"
        "  \n"
        "  return Object.keys(data).length > 0;\n"  # Line 30
        "}\n"
    )
    httpx_mock.add_response(method="GET", url=raw_url, text=file_content)

    # Load the fragment
    fragment = github_issue_loader(f"https://{enterprise_domain}/{owner}/{repo}/issues/{number}")
    content = str(fragment)

    # Verify the code is expanded into a code block
    assert "```ts" in content
    assert "export function validateInput(data: any): boolean {" in content
    assert "return Object.keys(data).length > 0;" in content

    # Verify the original URL is replaced
    assert f"https://{enterprise_domain}/{owner}/{repo}/blob/main/src/utils.ts#L25-L30" not in content

    # Check source URL is preserved with the enterprise domain
    assert fragment.source == f"https://{enterprise_domain}/{owner}/{repo}/issues/{number}"


def test_github_enterprise_token_auth_code_refs(httpx_mock, monkeypatch):
    # Test code references with GitHub token for GitHub Enterprise
    enterprise_domain = "github.enterprise.org"
    owner = "team"
    repo = "product"
    number = 8

    # Set GitHub token to test the API path for code references
    monkeypatch.setenv("GITHUB_TOKEN", "fake-enterprise-token")

    # Set up mock responses
    enterprise_api_base = f"https://{enterprise_domain}/api/v3"
    issue_api = f"{enterprise_api_base}/repos/{owner}/{repo}/issues/{number}"

    # Issue with code references
    issue_body = (
        "Bug found in:\n\n"
        f"https://{enterprise_domain}/{owner}/{repo}/blob/develop/lib/helpers.rb#L15-L18\n"
    )

    httpx_mock.add_response(
        method="GET",
        url=issue_api,
        json={
            "title": "Bug Report",
            "user": {"login": "tester"},
            "body": issue_body,
        },
    )

    # Mock comments
    comments_api = f"{issue_api}/comments?per_page=100"
    httpx_mock.add_response(method="GET", url=comments_api, json=[])

    # Mock API response for the file content (used when GITHUB_TOKEN is set)
    contents_api = f"{enterprise_api_base}/repos/{owner}/{repo}/contents/lib/helpers.rb?ref=develop"
    file_content = (
        "# Helper methods\n" * 14 +  # Lines 1-14
        "def calculate_totals(items)\n"  # Line 15
        "  items.sum do |item|\n"
        "    item[:price] * item[:quantity]\n"
        "  end\n"  # Line 18
        "end\n"
    )

    # Mock with raw content header used by GitHub API
    httpx_mock.add_response(
        method="GET",
        url=contents_api,
        headers={"Accept": "application/vnd.github.v3.raw"},
        text=file_content
    )

    # Load the fragment
    fragment = github_issue_loader(f"https://{enterprise_domain}/{owner}/{repo}/issues/{number}")
    content = str(fragment)

    # Verify the code block is included
    assert "```rb" in content
    assert "def calculate_totals(items)" in content
    assert "  end" in content

    # Verify the URL is replaced
    assert f"https://{enterprise_domain}/{owner}/{repo}/blob/develop/lib/helpers.rb#L15-L18" not in content
