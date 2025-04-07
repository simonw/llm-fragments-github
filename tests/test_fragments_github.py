from llm_fragments_github import github_loader


def test_plugin_is_installed():
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
