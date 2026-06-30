"""
jenkins_connector.py
Pulls test results / build logs from Jenkins so they can be fed into the
RAG ingestion pipeline (app/ingest.py).

Requires:
    pip install python-jenkins

Auth: Jenkins API token (Jenkins UI -> your user -> Configure -> API Token)
Env vars expected (see .env.example):
    JENKINS_URL
    JENKINS_USER
    JENKINS_API_TOKEN
"""

import os
import jenkins
from datetime import datetime


def get_client() -> jenkins.Jenkins:
    url = os.environ["JENKINS_URL"]
    user = os.environ["JENKINS_USER"]
    token = os.environ["JENKINS_API_TOKEN"]
    return jenkins.Jenkins(url, username=user, password=token)


def fetch_recent_build_console(job_name: str, build_number: int | str = "lastBuild") -> str:
    """Fetch the raw console log of a Jenkins build (this is what you'd parse
    with ingest.py if your HIL/CANoe results are printed to console output,
    or if you store result summaries there)."""
    server = get_client()
    return server.get_build_console_output(job_name, build_number)


def fetch_test_report(job_name: str, build_number: int | str = "lastBuild") -> dict:
    """Fetch the structured JUnit/test report attached to a Jenkins build,
    if your pipeline publishes one (e.g. via the JUnit or xUnit plugin)."""
    server = get_client()
    return server.get_build_test_report(job_name, build_number)


def save_console_logs_for_ingestion(
    job_name: str,
    output_dir: str,
    build_number: int | str = "lastBuild",
) -> str:
    """Pulls one build's console log and writes it to output_dir in the same
    plain-text format ingest.py expects (so you can drop this straight into
    data/sample_logs/ or point your real log dir at output_dir)."""
    console = fetch_recent_build_console(job_name, build_number)
    os.makedirs(output_dir, exist_ok=True)
    fname = f"{job_name}_{build_number}_{datetime.now():%Y%m%d_%H%M%S}.log"
    path = os.path.join(output_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(console)
    return path


def list_failing_jobs(job_name_prefix: str = "") -> list[dict]:
    """List jobs whose last build result was not SUCCESS — useful for a
    'show me today's failures' dashboard view."""
    server = get_client()
    jobs = server.get_all_jobs()
    failing = []
    for job in jobs:
        if job_name_prefix and not job["name"].startswith(job_name_prefix):
            continue
        info = server.get_job_info(job["name"])
        last_build = info.get("lastCompletedBuild")
        if not last_build:
            continue
        build_info = server.get_build_info(job["name"], last_build["number"])
        if build_info.get("result") != "SUCCESS":
            failing.append({
                "job": job["name"],
                "build_number": build_info["number"],
                "result": build_info.get("result"),
                "url": build_info.get("url"),
                "timestamp": build_info.get("timestamp"),
            })
    return failing


if __name__ == "__main__":
    # Example usage once env vars are set:
    # path = save_console_logs_for_ingestion("nightly_hil_regression", "../data/jenkins_logs")
    # print("Saved:", path)
    print("Set JENKINS_URL / JENKINS_USER / JENKINS_API_TOKEN, then call "
          "save_console_logs_for_ingestion(job_name, output_dir).")
