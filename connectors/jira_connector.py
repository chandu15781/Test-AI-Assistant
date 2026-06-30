"""
jira_connector.py
Used for Phase 1's "Is this a known issue?" capability — correlates a
failure signature with existing Jira defects, and can pull defect
descriptions into the RAG index as additional context.

Requires:
    pip install jira

Auth: Jira API token (https://id.atlassian.com/manage-profile/security/api-tokens)
Env vars expected (see .env.example):
    JIRA_URL              e.g. https://yourcompany.atlassian.net
    JIRA_USER_EMAIL
    JIRA_API_TOKEN
    JIRA_PROJECT_KEY      e.g. "ACC"
"""

import os
from jira import JIRA


def get_client() -> JIRA:
    return JIRA(
        server=os.environ["JIRA_URL"],
        basic_auth=(os.environ["JIRA_USER_EMAIL"], os.environ["JIRA_API_TOKEN"]),
    )


def search_known_issues(error_signature: str, max_results: int = 5) -> list[dict]:
    """Search Jira for tickets whose summary/description mention the given
    error signature (DTC code, signal name, error code, etc).

    error_signature examples: "ACC_ACTIVATION_TIMEOUT", "P189A00", "0x2345"
    """
    jira_client = get_client()
    project_key = os.environ.get("JIRA_PROJECT_KEY", "")
    jql_parts = [f'text ~ "{error_signature}"']
    if project_key:
        jql_parts.append(f'project = "{project_key}"')
    jql = " AND ".join(jql_parts) + " ORDER BY created DESC"

    issues = jira_client.search_issues(jql, maxResults=max_results)
    return [
        {
            "key": issue.key,
            "summary": issue.fields.summary,
            "status": issue.fields.status.name,
            "description": (issue.fields.description or "")[:500],
            "url": f"{os.environ['JIRA_URL']}/browse/{issue.key}",
        }
        for issue in issues
    ]


def fetch_issues_as_chunks(max_results: int = 50) -> list[dict]:
    """Fetch recent Jira issues from the configured project and convert them
    into embedding-ready dicts that rag_engine.build_index() can upsert into
    ChromaDB alongside local log files.

    Returns a list of dicts with keys:
        chunk_id, document (text to embed), metadata (source_file, test_case,
        build, ecu, verdict)
    """
    jira_client = get_client()
    project_key = os.environ.get("JIRA_PROJECT_KEY", "")
    jira_url = os.environ.get("JIRA_URL", "")

    jql = f'project = "{project_key}" ORDER BY created DESC' if project_key else "ORDER BY created DESC"

    try:
        issues = jira_client.search_issues(
            jql,
            maxResults=max_results,
            fields="summary,description,status,issuetype,priority,assignee,created,labels",
        )
    except Exception as e:
        print(f"[Jira] Failed to fetch issues: {e}")
        return []

    chunks = []
    for issue in issues:
        key = issue.key
        summary = issue.fields.summary or ""
        description = (issue.fields.description or "")[:1000]
        status = issue.fields.status.name if issue.fields.status else "Unknown"
        issuetype = issue.fields.issuetype.name if issue.fields.issuetype else "Issue"
        priority = issue.fields.priority.name if issue.fields.priority else "Unknown"
        labels = ", ".join(issue.fields.labels) if issue.fields.labels else ""
        url = f"{jira_url}/browse/{key}"

        # Build a rich text blob for embedding
        text = (
            f"Jira {issuetype}: {key}\n"
            f"Summary: {summary}\n"
            f"Status: {status} | Priority: {priority}\n"
            f"Labels: {labels}\n"
            f"URL: {url}\n\n"
            f"Description:\n{description}"
        )

        chunks.append({
            "chunk_id": f"jira_{key}",
            "document": text,
            "metadata": {
                "source_file": f"Jira:{key}",
                "test_case": summary[:80],
                "build": "jira",
                "ecu": labels or "unknown",
                "verdict": "FAIL" if issuetype in ("Bug", "Defect") else "UNKNOWN",
                "jira_key": key,
                "jira_url": url,
                "jira_status": status,
            },
        })

    return chunks


def create_defect_from_failure(
    test_case: str, build: str, root_cause_summary: str, log_excerpt: str
) -> str:
    """Auto-file a Jira defect from an AI-generated root cause summary.
    Use with a human-in-the-loop review step before calling this in
    production — don't auto-file without review initially."""
    jira_client = get_client()
    project_key = os.environ["JIRA_PROJECT_KEY"]

    issue_dict = {
        "project": {"key": project_key},
        "summary": f"[Auto-detected] {test_case} failed on build {build}",
        "description": (
            f"AI root cause summary:\n{root_cause_summary}\n\n"
            f"Log excerpt:\n{log_excerpt[:1000]}\n\n"
            f"(Filed automatically by Test Failure Analysis Assistant — please review.)"
        ),
        "issuetype": {"name": "Bug"},
    }
    new_issue = jira_client.create_issue(fields=issue_dict)
    return new_issue.key


if __name__ == "__main__":
    # Example:
    # results = search_known_issues("ACC_ACTIVATION_TIMEOUT")
    # for r in results:
    #     print(r["key"], r["summary"], r["url"])
    print("Set JIRA_URL / JIRA_USER_EMAIL / JIRA_API_TOKEN / JIRA_PROJECT_KEY, "
          "then call search_known_issues(error_signature).")
