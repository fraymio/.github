import os
import shutil
import subprocess
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--e2e",
        action="store_true",
        help="also run end to end tests (marked @pytest.mark.e2e)",
    )
    parser.addoption(
        "--int",
        action="store_true",
        help="also run integration tests (marked @pytest.mark.int)",
    )


def pytest_runtest_setup(item):
    if "e2e" in item.keywords and not item.config.getoption("--e2e"):
        pytest.skip("need --e2e option to run this test")
    if "int" in item.keywords and not item.config.getoption("--int"):
        pytest.skip("need --int option to run this test")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep the developer's shell env from leaking into review.py config."""
    for name in ("EXTRA_EXCLUDE", "OPENAI_MODEL", "REASONING_EFFORT", "DRY_RUN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def lock_patterns() -> list[str]:
    return ["*.lock", "package-lock.json", "go.sum"]


class GitRepo:
    def __init__(self, path: Path):
        self.path = path

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.path), "-c", "core.quotePath=true", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def write(self, rel: str, data: bytes = b"a\n") -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


@pytest.fixture
def git_repo(tmp_path, monkeypatch) -> GitRepo:
    """An empty git repo, isolated from the user's global/system git config
    (e.g. diff.noprefix or diff.mnemonicPrefix would change the headers)."""
    if not shutil.which("git"):
        pytest.skip("git not installed")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = GitRepo(tmp_path)
    repo.git("init", "-q")
    repo.git("config", "user.email", "test@example.com")
    repo.git("config", "user.name", "test")
    return repo
