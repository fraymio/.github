# .github

Org-wide GitHub config and tooling for fraymio.

## AI PR review

[`.github/workflows/pr-review.yml`](.github/workflows/pr-review.yml) is a reusable workflow that posts an OpenAI review (`gpt-6-astra` by default) as a comment on a PR as soon as it's opened, or when a draft is marked ready. It never approves or blocks. Bot PRs and PRs from forks are skipped.

**Opting a repo in:** add `.github/workflows/ai-pr-review.yml` to the repo. Either copy [`workflow-templates/ai-pr-review.yml`](workflow-templates/ai-pr-review.yml), or pick **AI PR review** under Actions → New workflow. Delete the file to opt out.

```yaml
on:
  pull_request:
    types: [opened, ready_for_review]
jobs:
  review:
    uses: fraymio/.github/.github/workflows/pr-review.yml@main
    permissions:
      contents: read
      pull-requests: write
    secrets: inherit
    # with:
    #   model: gpt-6-astra
    #   reasoning_effort: medium   # low | medium | high | xhigh | max
    #   exclude: "frontend/src/api_service/**, *.ipynb"   # on top of lock files + minified output
```

**Secret:** `OPENAI_API_KEY` as an org secret (Org settings → Secrets → Actions), shared with the opted-in repos. No other token is needed: each repo's built-in `GITHUB_TOKEN` posts the review.

**Local dry run:**

```sh
pip install -r pr_review/requirements.txt
GITHUB_TOKEN=... OPENAI_API_KEY=... REPOSITORY=fraymio/tessera PR_NUMBER=123 DRY_RUN=true \
  python pr_review/review.py
```

## Stale PR report

`stale_prs/pr_tracker.py` writes a CSV of PRs that have been open past the review SLA.
