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
  DRY_RUN           if "true", print the review instead of posting it
"""

import os

import requests

MAX_DIFF_CHARS = 400_000

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


def ask_model(pr: dict, diff: str) -> str:
    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS]
    user_input = (
        f"Repository: {REPOSITORY}\n"
        f"Title: {pr['title']}\n\n"
        f"Description:\n{pr.get('body') or '(none)'}\n\n"
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
