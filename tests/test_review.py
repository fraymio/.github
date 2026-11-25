"""Tests for diff path parsing and exclusion in pr_review/review.py."""

import pytest

import review


def section(header: str, *lines: str) -> str:
    return "\n".join([header, "index 1111111..2222222 100644", *lines]) + "\n"


HUNK = ("@@ -1 +1,2 @@", " a", "+b")

# Header shapes below are copied from real `git diff` output (core.quotePath=true).
CASES = [
    pytest.param(
        section("diff --git a/src/app.py b/src/app.py",
                "--- a/src/app.py", "+++ b/src/app.py", *HUNK),
        "src/app.py",
        id="ordinary-path",
    ),
    pytest.param(
        section("diff --git a/dir with space/uv.lock b/dir with space/uv.lock",
                "--- a/dir with space/uv.lock\t", "+++ b/dir with space/uv.lock\t", *HUNK),
        "dir with space/uv.lock",
        id="spaces-trailing-tab",
    ),
    pytest.param(
        section(r'diff --git "a/na\303\257ve/package-lock.json" "b/na\303\257ve/package-lock.json"',
                r'--- "a/na\303\257ve/package-lock.json"',
                r'+++ "b/na\303\257ve/package-lock.json"', *HUNK),
        "naïve/package-lock.json",
        id="non-ascii-octal-utf8",
    ),
    pytest.param(
        section(r'diff --git "a/tab\there/yarn.lock" "b/tab\there/yarn.lock"',
                r'--- "a/tab\there/yarn.lock"', r'+++ "b/tab\there/yarn.lock"', *HUNK),
        "tab\there/yarn.lock",
        id="tab-in-path",
    ),
    pytest.param(
        section(r'diff --git "a/quo\"te/go.sum" "b/quo\"te/go.sum"',
                r'--- "a/quo\"te/go.sum"', r'+++ "b/quo\"te/go.sum"', *HUNK),
        'quo"te/go.sum',
        id="double-quote-in-path",
    ),
    pytest.param(
        "\n".join([r'diff --git "a/na\303\257ve/new.lock" "b/na\303\257ve/new.lock"',
                   "new file mode 100644", "index 0000000..3e75765",
                   "--- /dev/null", r'+++ "b/na\303\257ve/new.lock"', "@@ -0,0 +1 @@", "+new"]) + "\n",
        "naïve/new.lock",
        id="new-file",
    ),
    pytest.param(
        "\n".join([r'diff --git "a/na\303\257ve/old.lock" "b/na\303\257ve/old.lock"',
                   "deleted file mode 100644", "index 7898192..0000000",
                   r'--- "a/na\303\257ve/old.lock"', "+++ /dev/null", "@@ -1 +0,0 @@", "-a"]) + "\n",
        "naïve/old.lock",
        id="deleted-file",
    ),
    pytest.param(
        "\n".join(["diff --git a/gone.lock \"b/na\\303\\257ve/renamed.lock\"",
                   "similarity index 100%", "rename from gone.lock",
                   r'rename to "na\303\257ve/renamed.lock"']) + "\n",
        "naïve/renamed.lock",
        id="rename-mixed-quoting",
    ),
    pytest.param(
        "\n".join([r'diff --git "a/na\303\257ve/bin.lock" "b/na\303\257ve/bin.lock"',
                   "index 1111111..2222222 100644",
                   r'Binary files "a/na\303\257ve/bin.lock" and "b/na\303\257ve/bin.lock" differ']) + "\n",
        "naïve/bin.lock",
        id="binary-quoted",
    ),
    pytest.param(
        "\n".join(["diff --git a/my b/dir/x.lock b/my b/dir/x.lock",
                   "old mode 100644", "new mode 100755"]) + "\n",
        "my b/dir/x.lock",
        id="mode-only-spaces",
    ),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_section_path(text, expected):
    assert review.section_path(text) == expected


def test_content_lines_are_not_mistaken_for_headers():
    # A removed line "-- a/x" renders as "--- a/x" inside a hunk.
    text = section("diff --git a/src/app.py b/src/app.py",
                   "--- a/src/app.py", "+++ b/src/app.py",
                   "@@ -1 +1 @@", "--- a/uv.lock", "+++ b/uv.lock")
    assert review.section_path(text) == "src/app.py"


def test_filter_quoted_and_ordinary_paths(lock_patterns):
    diff = "".join(case.values[0] for case in CASES)
    kept, skipped = review.filter_diff(diff, lock_patterns)
    assert skipped == [case.values[1] for case in CASES if case.values[1] != "src/app.py"]
    assert kept.count("diff --git ") == 1
    assert "diff --git a/src/app.py b/src/app.py" in kept


def test_path_patterns_match_decoded_paths():
    diff = section(r'diff --git "a/gen/caf\303\251/client.ts" "b/gen/caf\303\251/client.ts"',
                   r'--- "a/gen/caf\303\251/client.ts"', r'+++ "b/gen/caf\303\251/client.ts"', *HUNK)
    _, skipped = review.filter_diff(diff, ["gen/café/*"])
    assert skipped == ["gen/café/client.ts"]


def test_matching_is_case_sensitive():
    diff = section("diff --git a/UV.LOCK b/UV.LOCK", "--- a/UV.LOCK", "+++ b/UV.LOCK", *HUNK)
    _, skipped = review.filter_diff(diff, ["*.lock"])
    assert skipped == []


def test_extra_exclude_env(monkeypatch):
    monkeypatch.setenv("EXTRA_EXCLUDE", "docs/**,\n *.ipynb ")
    patterns = review.exclude_patterns()
    assert {"docs/**", "*.ipynb", "package-lock.json"} <= set(patterns)


@pytest.mark.int
def test_real_git_diff_with_awkward_paths(git_repo, lock_patterns):
    """End to end against whatever this machine's git actually emits."""
    locks = ["dir with space/uv.lock", "naïve/package-lock.json",
             "tab\there/yarn.lock", 'quo"te/go.sum', "naïve/bin.lock"]
    for rel in locks + ["src/app.py", "naïve/old.lock"]:
        git_repo.write(rel, b"\x00\x01" if rel.endswith("bin.lock") else b"a\n")
    git_repo.git("add", "-A")
    git_repo.git("commit", "-qm", "init")

    for rel in locks + ["src/app.py"]:
        git_repo.write(rel, b"\x00\x02" if rel.endswith("bin.lock") else b"a\nb\n")
    git_repo.git("mv", "naïve/old.lock", "naïve/renamed.lock")
    git_repo.write("naïve/new.lock", b"new\n")
    git_repo.git("add", "-A")

    diff = git_repo.git("diff", "--cached", "-M")
    kept, skipped = review.filter_diff(diff, lock_patterns)

    assert sorted(skipped) == sorted(locks + ["naïve/renamed.lock", "naïve/new.lock"])
    assert kept.count("diff --git ") == 1
    assert "src/app.py" in kept
