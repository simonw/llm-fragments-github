from llm_fragments_github import github_loader, github_issue_loader
import pytest


def test_github_loader():
    fragments = github_loader("simonw/test-repo-for-llm-fragments-github")
    assert [(fragment.source, str(fragment)) for fragment in fragments] == [
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
        == "Issue fragments must be issue:owner/repo/NUMBER or a full GitHub issue URL – received 'This is bad'"
    )
