"""
Reviews one pull request with an OpenAI model and posts the result as a PR
review comment. Called by .github/workflows/pr-review.yml.

Env vars:
  GITHUB_TOKEN      token with pull-request write on the repo (required)
  OPENAI_API_KEY    OpenAI API key (required)
  REPOSITORY        owner/name, e.g. fraymio/tessera (required)
  PR_NUMBER         pull request number (required)
  OPENAI_MODEL      model ID (default: gpt-6-astra)
  REASONING_EFFORT  low | medium | high | xhigh | max (default: medium)
  EXTRA_EXCLUDE     extra glob patterns to drop from the diff, comma or newline separated
  DRY_RUN           if "true", print the review instead of posting it
"""

import os
import re
from fnmatch import fnmatchcase

import requests

MAX_DIFF_CHARS = 400_000

# Files that are generated or carry no reviewable logic. Patterns without a "/"
# match the file name anywhere in the tree; patterns with one match the full path.
DEFAULT_EXCLUDE = [
    # Lock files
    "*.lock",  # yarn, poetry, uv, pdm, pixi, Cargo, Gemfile, composer, flake, Podfile
    "terraform.lock.hcl",  # Terraform lock file
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "bun.lockb",
    "packages.lock.json",
    "conda-lock.yml",
    "*.conda-lock.yml",
    "go.sum",
    # Minified / build output
    "*.min.js",
    "*.min.css",
    "*.map",
]

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
REPOSITORY = os.environ.get("REPOSITORY")
PR_NUMBER = os.environ.get("PR_NUMBER")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL") or "gpt-6-astra"
REASONING_EFFORT = os.environ.get("REASONING_EFFORT") or "medium"
DRY_RUN = os.environ.get("DRY_RUN", "").lower() == "true"

GH = "https://api.github.com"
gh_headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

SYSTEM_PROMPT = """You are a senior engineer reviewing a pull request.
Focus on real problems: bugs, incorrect logic, security issues, data loss,
race conditions, broken error handling, and missing tests for risky changes.
Skip style nits and anything a linter would catch. Do not praise the PR.

Format your answer in GitHub markdown:
- Start with a 1-2 sentence summary of what the PR does.
- Then a "Findings" list, most severe first. For each: `path:line`, what is
  wrong, and a concrete fix. If there are no meaningful problems, say so plainly.
Keep it short."""


def get_pr(pr_url: str) -> dict:
    resp = requests.get(pr_url, headers=gh_headers, timeout=60)
    resp.raise_for_status()
    return resp.json()


def get_diff(pr_url: str) -> str:
    resp = requests.get(
        pr_url,
        headers={**gh_headers, "Accept": "application/vnd.github.diff"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.text


def exclude_patterns() -> list[str]:
    extra = os.environ.get("EXTRA_EXCLUDE") or ""
    return DEFAULT_EXCLUDE + [p.strip() for p in re.split(r"[,\n]", extra) if p.strip()]


def is_excluded(path: str, patterns: list[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatchcase(path if "/" in p else name, p) for p in patterns)


_C_ESCAPES = {
    "a": 0x07,
    "b": 0x08,
    "f": 0x0C,
    "n": 0x0A,
    "r": 0x0D,
    "t": 0x09,
    "v": 0x0B,
    '"': 0x22,
    "\\": 0x5C,
}


def _read_quoted(s: str, i: int) -> tuple[str, int]:
    """Decode a Git C-style quoted string starting at s[i] == '"'.

    Git escapes \\ " and control chars, and writes non-ASCII bytes as \\ooo
    octal, so the result is decoded from UTF-8 bytes. Returns (text, index
    just past the closing quote).
    """
    out = bytearray()
    i += 1
    while i < len(s):
        c = s[i]
        if c == '"':
            return out.decode("utf-8", errors="replace"), i + 1
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in "01234567":
                octal = re.match(r"[0-7]{1,3}", s[i + 1 :]).group()
                out.append(int(octal, 8) & 0xFF)
                i += 1 + len(octal)
                continue
            out.append(_C_ESCAPES.get(nxt, ord(nxt)))
            i += 2
            continue
        out += c.encode("utf-8")
        i += 1
    raise ValueError(f"unterminated quoted path: {s!r}")


def _unquote_path(raw: str) -> str:
    """Decode one path field from a ---/+++/rename/copy header line."""
    if raw.startswith('"'):
        return _read_quoted(raw, 0)[0]
    # Git appends a TAB after unquoted names that contain a space.
    return raw.rstrip("\t")


def _strip_prefix(path: str) -> str:
    return path[2:] if path[:2] in ("a/", "b/") else path


def _paths_from_git_line(rest: str) -> tuple[str, str] | None:
    """Parse the '<a-path> <b-path>' part of a 'diff --git' line."""
    paths, i = [], 0
    while i < len(rest) and len(paths) < 2:
        if rest[i] == '"':
            path, i = _read_quoted(rest, i)
            paths.append(path)
            i += 1  # separating space
        elif len(paths) == 0 and '"' in rest:
            # Unquoted a-path followed by a quoted b-path.
            end = rest.index(' "', i)
            paths.append(rest[i:end])
            i = end + 1
        elif len(paths) == 1:
            paths.append(rest[i:])
            break
        else:
            # Both unquoted; paths may contain spaces. For non-renames the
            # two halves are identical, which makes the split unambiguous.
            half = (len(rest) - 1) // 2
            a, b = rest[:half], rest[half + 1 :]
            if rest[half] == " " and a[2:] == b[2:]:
                return _strip_prefix(a), _strip_prefix(b)
            m = re.match(r"(a/.+?) (b/.+)$", rest)
            return (_strip_prefix(m.group(1)), _strip_prefix(m.group(2))) if m else None
    if len(paths) != 2:
        return None
    return _strip_prefix(paths[0]), _strip_prefix(paths[1])


def section_path(section: str) -> str | None:
    """Return the file path a per-file diff section applies to.

    Prefers the unambiguous header lines (+++, ---, rename to, copy to) over
    the 'diff --git' line, whose two paths can't always be split apart.
    """
    first, *lines = section.split("\n")
    new_path = old_path = moved_to = None
    for line in lines:
        if line.startswith(("@@", "Binary files ", "GIT binary patch")):
            break  # end of the header block; content follows
        if line.startswith("+++ "):
            raw = line[4:]
            if raw != "/dev/null":
                new_path = _strip_prefix(_unquote_path(raw))
        elif line.startswith("--- "):
            raw = line[4:]
            if raw != "/dev/null":
                old_path = _strip_prefix(_unquote_path(raw))
        elif line.startswith(("rename to ", "copy to ")):
            moved_to = _unquote_path(line.split(" to ", 1)[1])
    if new_path or old_path or moved_to:
        return new_path or moved_to or old_path
    parsed = _paths_from_git_line(first[len("diff --git ") :])
    return parsed[1] if parsed else None


def filter_diff(diff: str, patterns: list[str] | None = None) -> tuple[str, list[str]]:
    """Drop per-file sections whose path matches an exclude pattern."""
    if patterns is None:
        patterns = exclude_patterns()
    kept, skipped = [], []
    # Each file's section starts with a "diff --git" line.
    for section in re.split(r"(?m)^(?=diff --git )", diff):
        path = section_path(section) if section.startswith("diff --git ") else None
        if path is not None and is_excluded(path, patterns):
            skipped.append(path)
        else:
            kept.append(section)
    return "".join(kept), skipped


def ask_model(pr: dict, diff: str) -> str:
    diff, skipped = filter_diff(diff)
    if skipped:
        print(f"Excluded from review: {', '.join(skipped)}")
    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS]
    skipped_note = (
        "Files changed but omitted from the diff (generated/lock files, do not "
        f"review): {', '.join(skipped)}\n\n"
        if skipped
        else ""
    )
    user_input = (
        f"Repository: {REPOSITORY}\n"
        f"Title: {pr['title']}\n\n"
        f"Description:\n{pr.get('body') or '(none)'}\n\n"
        f"{skipped_note}"
        f"Diff{' (truncated)' if truncated else ''}:\n```diff\n{diff}\n```"
    )
    resp = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": OPENAI_MODEL,
            "instructions": SYSTEM_PROMPT,
            "input": user_input,
            "reasoning": {"effort": REASONING_EFFORT},
            "max_output_tokens": 64_000,
        },
        timeout=1500,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text}")
    data = resp.json()
    text = "".join(
        part["text"]
        for item in data.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ).strip()
    if not text:
        raise RuntimeError(f"OpenAI returned no text: {data}")
    return text


def post_review(pr_url: str, pr: dict, review: str) -> None:
    body = (
        f"### AI review ({OPENAI_MODEL})\n\n{review}\n\n"
        f"<sub>Reviewed at `{pr['head']['sha'][:7]}`. "
        f"Automated review; use your judgment.</sub>"
    )
    if DRY_RUN:
        print(body)
        return
    resp = requests.post(
        f"{pr_url}/reviews",
        headers=gh_headers,
        json={"body": body, "event": "COMMENT", "commit_id": pr["head"]["sha"]},
        timeout=60,
    )
    resp.raise_for_status()


def main() -> None:
    for name in ("GITHUB_TOKEN", "OPENAI_API_KEY", "REPOSITORY", "PR_NUMBER"):
        assert os.environ.get(name), f"{name} is required"

    pr_url = f"{GH}/repos/{REPOSITORY}/pulls/{PR_NUMBER}"
    pr = get_pr(pr_url)
    print(f"Reviewing {REPOSITORY}#{PR_NUMBER}: {pr['title']}")
    post_review(pr_url, pr, ask_model(pr, get_diff(pr_url)))


if __name__ == "__main__":
    main()
