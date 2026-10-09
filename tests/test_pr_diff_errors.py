import pytest

from llm_fragments_github import github_pr_loader


@pytest.mark.parametrize("status_code", [200, 406, 403, 404, 500])
def test_pr_diff_response(httpx_mock, status_code):
    api = "https://api.github.com/repos/example/repository/pulls/1"
    httpx_mock.add_response(
        url=api,
        json={
            "title": "Example PR",
            "user": {"login": "alice"},
            "body": "PR description",
        },
    )
    httpx_mock.add_response(url=api + "/comments?per_page=100", json=[])
    httpx_mock.add_response(
        url=api + ".diff", status_code=status_code, text="diff contents"
    )

    if status_code not in (200, 406):
        with pytest.raises(
            ValueError, match=rf"GitHub API request failed \[{status_code}\]"
        ):
            github_pr_loader("example/repository/1")
        return

    fragments = github_pr_loader("example/repository/1")
    assert "PR description" in str(fragments[0])
    assert fragments[0].source == "https://github.com/example/repository/pull/1"
    if status_code == 406:
        assert len(fragments) == 1
    else:
        assert len(fragments) == 2
        assert str(fragments[1]) == "diff contents"
