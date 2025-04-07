# llm-fragments-github

[![PyPI](https://img.shields.io/pypi/v/llm-fragments-github.svg)](https://pypi.org/project/llm-fragments-github/)
[![Changelog](https://img.shields.io/github/v/release/simonw/llm-fragments-github?include_prereleases&label=changelog)](https://github.com/simonw/llm-fragments-github/releases)
[![Tests](https://github.com/simonw/llm-fragments-github/actions/workflows/test.yml/badge.svg)](https://github.com/simonw/llm-fragments-github/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/llm-fragments-github/blob/main/LICENSE)

Load GitHub repository contents as fragments

## Installation

Install this plugin in the same environment as [LLM](https://llm.datasette.io/).
```bash
llm install llm-fragments-github
```
## Usage

Use `-f github:user/repo` to include every text file from the specified GitHub repo as a fragment. For example:
```bash
llm -f github:simonw/files-to-prompt 'suggest new features for this tool'
```

## Development

To set up this plugin locally, first checkout the code. Then create a new virtual environment:
```bash
cd llm-fragments-github
python -m venv venv
source venv/bin/activate
```
Now install the dependencies and test dependencies:
```bash
llm install -e '.[test]'
```
To run the tests:
```bash
python -m pytest
```
