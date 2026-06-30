"""
polarion_connector.py
For Phase 2 (Test Knowledge Assistant) but included now since the PDF's
roadmap leads here next. Pulls requirements / test specs / linked test
cases from Polarion via its REST API (Polarion 21+ has a native REST API;
older versions need the SOAP/OSLC API — see notes below).

Requires:
    pip install requests   (already a dependency)

Auth: Personal Access Token (Polarion -> User Settings -> Access Tokens)
Env vars expected:
    POLARION_URL          e.g. https://polarion.yourcompany.com/polarion
    POLARION_TOKEN
    POLARION_PROJECT_ID
"""

import os
import requests


def _headers():
    return {
        "Authorization": f"Bearer {os.environ['POLARION_TOKEN']}",
        "Accept": "application/json",
    }


def _base_url():
    return f"{os.environ['POLARION_URL']}/rest/v1/projects/{os.environ['POLARION_PROJECT_ID']}"


def fetch_requirement(work_item_id: str) -> dict:
    """Fetch a single requirement / work item by ID (e.g. 'REQ-1042')."""
    url = f"{_base_url()}/workitems/{work_item_id}"
    resp = requests.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()["data"]
    return {
        "id": data["id"],
        "title": data["attributes"].get("title"),
        "description": data["attributes"].get("description", {}).get("value", ""),
        "type": data["attributes"].get("type"),
        "status": data["attributes"].get("status"),
    }


def fetch_test_cases_for_feature(feature_query: str, max_results: int = 50) -> list[dict]:
    """Search test case work items, e.g. feature_query='Adaptive Cruise Control'.
    Polarion's query language (Lucene-based) goes in the `query` param."""
    url = f"{_base_url()}/workitems"
    params = {
        "query": f'type:testcase AND title:"{feature_query}"',
        "page[size]": max_results,
    }
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("data", [])
    return [
        {
            "id": item["id"],
            "title": item["attributes"].get("title"),
            "status": item["attributes"].get("status"),
        }
        for item in items
    ]


def fetch_linked_requirements(test_case_id: str) -> list[dict]:
    """Fetch requirements linked to a test case (for 'which requirements does
    this test cover?' queries)."""
    url = f"{_base_url()}/workitems/{test_case_id}/linkedworkitems"
    resp = requests.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
    links = resp.json().get("data", [])
    return [
        {"id": link["id"], "role": link.get("attributes", {}).get("role")}
        for link in links
    ]


def export_requirements_for_rag(feature_query: str, output_dir: str) -> int:
    """Pulls test cases + their linked requirements for a feature and writes
    them as plain-text docs ingest.py-compatible files, ready for the Phase 2
    RAG index (separate collection from Phase 1 failure logs)."""
    os.makedirs(output_dir, exist_ok=True)
    test_cases = fetch_test_cases_for_feature(feature_query)
    count = 0
    for tc in test_cases:
        linked = fetch_linked_requirements(tc["id"])
        text = (
            f"[Test Case] {tc['id']} - {tc['title']}\n"
            f"[Status] {tc['status']}\n"
            f"[Linked Requirements] {', '.join(l['id'] for l in linked)}\n"
        )
        path = os.path.join(output_dir, f"{tc['id']}.txt")
        with open(path, "w") as f:
            f.write(text)
        count += 1
    return count


# ---------------------------------------------------------------------------
# NOTE: If your Polarion instance is older and only exposes SOAP/OSLC, the
# REST calls above won't work. In that case use Polarion's OSLC API instead
# (different auth flow, XML-based) - the official Polarion ALM "OSLC" docs
# cover this. The function signatures here would stay the same; only the
# internals change.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Set POLARION_URL / POLARION_TOKEN / POLARION_PROJECT_ID, then call "
          "export_requirements_for_rag('Adaptive Cruise Control', '../data/polarion_docs').")
