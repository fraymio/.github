"""Tests for diff path parsing and exclusion. Run: python -m unittest discover pr_review"""

import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    import requests  # noqa: F401
except ImportError:  # parsing tests don't need the network client
    sys.modules["requests"] = types.ModuleType("requests")

import review  # noqa: E402

LOCKS = ["*.lock", "package-lock.json", "go.sum"]


def section(header: str, *lines: str) -> str:
    return "\n".join([header, "index 1111111..2222222 100644", *lines]) + "\n"


def hunk() -> list[str]:
    return ["@@ -1 +1,2 @@", " a", "+b"]


# Header shapes below are copied from real `git diff` output (core.quotePath=true).
CASES = [
    # (description, section text, expected path)
    (
        "ordinary path",
        section("diff --git a/src/app.py b/src/app.py",
                "--- a/src/app.py", "+++ b/src/app.py", *hunk()),
        "src/app.py",
    ),
    (
        "unquoted path with spaces (git appends a TAB on ---/+++)",
        section("diff --git a/dir with space/uv.lock b/dir with space/uv.lock",
                "--- a/dir with space/uv.lock\t", "+++ b/dir with space/uv.lock\t", *hunk()),
        "dir with space/uv.lock",
    ),
    (
        "non-ASCII path as octal-escaped UTF-8",
        section(r'diff --git "a/na\303\257ve/package-lock.json" "b/na\303\257ve/package-lock.json"',
                r'--- "a/na\303\257ve/package-lock.json"',
                r'+++ "b/na\303\257ve/package-lock.json"', *hunk()),
        "naïve/package-lock.json",
    ),
    (
        "tab in path",
        section(r'diff --git "a/tab\there/yarn.lock" "b/tab\there/yarn.lock"',
                r'--- "a/tab\there/yarn.lock"', r'+++ "b/tab\there/yarn.lock"', *hunk()),
        "tab\there/yarn.lock",
    ),
    (
        "double quote in path",
        section(r'diff --git "a/quo\"te/go.sum" "b/quo\"te/go.sum"',
                r'--- "a/quo\"te/go.sum"', r'+++ "b/quo\"te/go.sum"', *hunk()),
        'quo"te/go.sum',
    ),
    (
        "new file (--- /dev/null)",
        "\n".join([r'diff --git "a/na\303\257ve/new.lock" "b/na\303\257ve/new.lock"',
                   "new file mode 100644", "index 0000000..3e75765",
                   "--- /dev/null", r'+++ "b/na\303\257ve/new.lock"', "@@ -0,0 +1 @@", "+new"]) + "\n",
        "naïve/new.lock",
    ),
    (
        "deleted file (+++ /dev/null)",
        "\n".join([r'diff --git "a/na\303\257ve/old.lock" "b/na\303\257ve/old.lock"',
                   "deleted file mode 100644", "index 7898192..0000000",
                   r'--- "a/na\303\257ve/old.lock"', "+++ /dev/null", "@@ -1 +0,0 @@", "-a"]) + "\n",
        "naïve/old.lock",
    ),
    (
        "pure rename: unquoted old path, quoted new path, no ---/+++",
        "\n".join(["diff --git a/gone.lock \"b/na\\303\\257ve/renamed.lock\"",
                   "similarity index 100%", "rename from gone.lock",
                   r'rename to "na\303\257ve/renamed.lock"']) + "\n",
        "naïve/renamed.lock",
    ),
    (
        "binary change: only the diff --git line names the file",
        "\n".join([r'diff --git "a/na\303\257ve/bin.lock" "b/na\303\257ve/bin.lock"',
                   "index 1111111..2222222 100644",
                   r'Binary files "a/na\303\257ve/bin.lock" and "b/na\303\257ve/bin.lock" differ']) + "\n",
        "naïve/bin.lock",
    ),
    (
        "mode-only change with spaces in path: diff --git line split by symmetry",
        "\n".join(["diff --git a/my b/dir/x.lock b/my b/dir/x.lock",
                   "old mode 100644", "new mode 100755"]) + "\n",
        "my b/dir/x.lock",
    ),
]


class SectionPathTest(unittest.TestCase):
    def test_cases(self):
        for desc, text, expected in CASES:
            with self.subTest(desc):
                self.assertEqual(review.section_path(text), expected)

    def test_content_lines_are_not_mistaken_for_headers(self):
        # A removed line "-- a/x" renders as "--- a/x" inside a hunk.
        text = section("diff --git a/src/app.py b/src/app.py",
                       "--- a/src/app.py", "+++ b/src/app.py",
                       "@@ -1 +1 @@", "--- a/uv.lock", "+++ b/uv.lock")
        self.assertEqual(review.section_path(text), "src/app.py")


class FilterDiffTest(unittest.TestCase):
    def test_quoted_and_ordinary_paths(self):
        diff = "".join(text for _, text, _ in CASES)
        kept, skipped = review.filter_diff(diff, LOCKS)
        self.assertEqual(
            skipped,
            [path for desc, _, path in CASES if path != "src/app.py"],
        )
        self.assertIn("diff --git a/src/app.py b/src/app.py", kept)
        self.assertEqual(kept.count("diff --git "), 1)

    def test_path_patterns_match_decoded_paths(self):
        diff = section(r'diff --git "a/gen/caf\303\251/client.ts" "b/gen/caf\303\251/client.ts"',
                       r'--- "a/gen/caf\303\251/client.ts"', r'+++ "b/gen/caf\303\251/client.ts"', *hunk())
        _, skipped = review.filter_diff(diff, ["gen/café/*"])
        self.assertEqual(skipped, ["gen/café/client.ts"])

    def test_matching_is_case_sensitive(self):
        diff = section("diff --git a/UV.LOCK b/UV.LOCK", "--- a/UV.LOCK", "+++ b/UV.LOCK", *hunk())
        _, skipped = review.filter_diff(diff, ["*.lock"])
        self.assertEqual(skipped, [])

    def test_extra_exclude_env(self):
        with unittest.mock.patch.dict("os.environ", {"EXTRA_EXCLUDE": "docs/**,\n *.ipynb "}):
            patterns = review.exclude_patterns()
        self.assertIn("docs/**", patterns)
        self.assertIn("*.ipynb", patterns)
        self.assertIn("package-lock.json", patterns)


@unittest.skipUnless(shutil.which("git"), "git not installed")
class RealGitDiffTest(unittest.TestCase):
    """End to end against whatever this machine's git actually emits."""

    def git(self, *args):
        return subprocess.run(["git", "-C", self.repo, "-c", "core.quotePath=true", *args],
                              check=True, capture_output=True, text=True).stdout

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")

    def write(self, rel, data=b"a\n"):
        p = Path(self.repo, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def test_awkward_paths(self):
        locks = ["dir with space/uv.lock", "naïve/package-lock.json",
                 "tab\there/yarn.lock", 'quo"te/go.sum', "naïve/bin.lock"]
        for rel in locks + ["src/app.py", "naïve/old.lock"]:
            self.write(rel, b"\x00\x01" if rel.endswith("bin.lock") else b"a\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")

        for rel in locks + ["src/app.py"]:
            self.write(rel, b"\x00\x02" if rel.endswith("bin.lock") else b"a\nb\n")
        self.git("mv", "naïve/old.lock", "naïve/renamed.lock")
        self.write("naïve/new.lock", b"new\n")
        self.git("add", "-A")

        diff = self.git("diff", "--cached", "-M")
        kept, skipped = review.filter_diff(diff, LOCKS)

        self.assertCountEqual(skipped, locks + ["naïve/renamed.lock", "naïve/new.lock"])
        self.assertEqual(kept.count("diff --git "), 1)
        self.assertIn("src/app.py", kept)


if __name__ == "__main__":
    unittest.main()
