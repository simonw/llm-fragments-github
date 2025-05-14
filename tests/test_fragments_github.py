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
        == "# Example PR\n\n*Posted by @simonw*\n\nThis is an example PR.\n\n---\n\n### Comment by @simonw\n\nIt has one comment.\n\n---\n"
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
