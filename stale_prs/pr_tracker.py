"""
Generates a report of PRs in the fraymio org that have not had a completed review
over a preset number of days
"""

from pathlib import Path
import requests
from datetime import datetime, timedelta
import csv
import os

import holidays
import dotenv


# GitHub API parameters
dotenv.load_dotenv()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
assert (
    GITHUB_TOKEN is not None
), f"Environment variable GITHUB_TOKEN cannot be None. \
    Is it in your .env file?"

ORG_NAME = "fraymio"
# Number of days before it's on the list
SLA = 5

REPO_BLACKLIST = [
    "interpr",
    "inprocessing",
    "validation",
    "corefraym",
    "wiki-via-tf",
    "sample-design",
    "covfraym",
]

# Headers for GitHub API request
headers = {"Authorization": f"token {GITHUB_TOKEN}"}

# GitHub API base URL for listing repositories under an organization
repos_url = f"https://api.github.com/orgs/{ORG_NAME}/repos"


def get_business_days_difference(start_date, end_date):
    """Calculate business days difference between two dates."""
    us_holidays = holidays.US()  # Adjust this for your locale
    delta = end_date - start_date
    business_days = 0
    for i in range(delta.days + 1):
        day = start_date + timedelta(days=i)
        if (
            day.weekday() < 5 and day not in us_holidays
        ):  # Exclude weekends and holidays
            business_days += 1
    return business_days


def get_open_prs_for_repo(repo_name, csv_writer):
    """Fetch and list open PRs for a specific repository, handling pagination."""
    page = 1
    while True:
        url = f"https://api.github.com/repos/{ORG_NAME}/{repo_name}/pulls?page={page}&per_page=100"
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            print(
                f"Failed to fetch PRs for repo {repo_name}: {response.status_code} - {response.text}"
            )
            break
        open_prs = response.json()
        if not open_prs:
            break  # No more PRs, exit the loop

        today = datetime.utcnow()

        for pr in open_prs:
            created_at = datetime.strptime(pr["created_at"], "%Y-%m-%dT%H:%M:%SZ")
            business_days_open = get_business_days_difference(created_at, today)

            if business_days_open > SLA:
                pr_branch: str = pr["head"]["ref"]
                if pr_branch.startswith("dependabot"):
                    return
                pr_link = pr["html_url"]
                time_open = today - created_at

                # Write data to CSV
                csv_writer.writerow([repo_name, pr_branch, pr_link, time_open.days])

        page += 1  # Fetch the next page


def list_old_open_prs_for_org(output_file):
    """Fetch all repositories under an organization, handling pagination, and list old open PRs in a CSV."""
    page = 1
    with open(output_file, mode="w", newline="", encoding="utf-8") as file:
        csv_writer = csv.writer(file)
        csv_writer.writerow(["repo", "branch", "pr_link", "days_open"])

        while True:
            response = requests.get(
                f"{repos_url}?page={page}&per_page=100", headers=headers
            )
            if response.status_code == 200:
                repos = response.json()
                if not repos:
                    break  # No more repositories, exit the loop

                for repo in repos:
                    repo_name = repo["name"]
                    if repo_name in REPO_BLACKLIST:
                        continue
                    get_open_prs_for_repo(repo_name, csv_writer)

                page += 1  # Fetch the next page of repositories
            else:
                print(
                    f"Failed to fetch repositories: {response.status_code} - {response.text}"
                )
                break


if __name__ == "__main__":
    script_path = Path(os.path.realpath(__file__)).parent
    output_file = Path(
        f"{script_path}/output/open_prs_{datetime.now().strftime('%Y-%m-%d_%H,%M,%S')}.csv"
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    list_old_open_prs_for_org(output_file)
    print(f"Data written to {output_file}")
