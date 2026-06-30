"""
streamlit_app.py
UI for the Phase-1 Failure Analysis Assistant.

Run with:
    streamlit run streamlit_app.py
"""

import os
import sys
import time
import streamlit as st
from dotenv import load_dotenv
import importlib

# Load .env file FIRST, before any other imports
# Clear old env vars first
for var in ['JENKINS_URL', 'JENKINS_USER', 'JENKINS_API_TOKEN', 
            'JIRA_URL', 'JIRA_USER_EMAIL', 'JIRA_API_TOKEN', 'JIRA_PROJECT_KEY',
            'POLARION_URL', 'POLARION_TOKEN', 'CANOE_LOG_PATH']:
    os.environ.pop(var, None)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

sys.path.insert(0, os.path.dirname(__file__))
import rag_engine
# Force reload to ensure fresh module with updated env vars
importlib.reload(rag_engine)
from ingest import load_logs_from_dir

st.set_page_config(
    page_title="Test Failure Analysis Assistant",
    page_icon="🔧",
    layout="wide",
)
# every sample log file getting into log_dir

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "sample_logs")

# ---------------------------------------------------------------------------
# Sidebar: index management + connector status
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🔧 Test AI Assistant")
    st.caption("Phase 1 · Failure Analysis · Open-source stack")

    st.markdown("### Knowledge base")
    if st.button("🔄 (Re)build index from log folder", use_container_width=True):
        with st.spinner("Parsing logs and building embeddings..."):
            try:
                result = rag_engine.build_index(LOG_DIR)
                if isinstance(result, tuple):
                    log_count, jira_count = result
                    msg = f"Indexed {log_count} log file(s)."
                    if jira_count:
                        msg += f" Also pulled {jira_count} Jira issue(s) into the index."
                    st.success(msg)
                else:
                    st.success(f"Indexed {result} log files.")
            except Exception as e:
                st.error(f"Indexing failed: {e}")

    try:
        collection = rag_engine.get_or_create_collection()
        st.metric("Chunks indexed", collection.count())
    except Exception:
        st.metric("Chunks indexed", "—")

    st.markdown("---")
    st.markdown("### Connector status")
    st.caption("Mock mode unless real credentials are set in `.env`")
    connector_status = {
        "CANoe / CAPL logs": os.environ.get("CANOE_LOG_PATH") is not None,
        "Jenkins": os.environ.get("JENKINS_URL") is not None,
        "Jira": os.environ.get("JIRA_URL") is not None,
        "Polarion": os.environ.get("POLARION_URL") is not None,
    }
    for name, connected in connector_status.items():
        icon = "🟢" if connected else "⚪"
        label = "live" if connected else "not configured"
        st.markdown(f"{icon} **{name}** — {label}")

    st.markdown("---")
    st.markdown("### LLM backend")
    api_key_status = "✓ configured" if rag_engine.GROQ_API_KEY else "✗ not set"
    st.code(f"Groq API\nmodel: {rag_engine.GROQ_MODEL}\nstatus: {api_key_status}", language="text")

# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------
st.title("Test Failure Analysis Assistant")
st.caption(
    "Ask about any failed test case. The assistant retrieves similar past "
    "failures and explains the likely root cause."
)

example_cols = st.columns(4)
example_questions = [
    "Why did TC_ACC_102 fail?",
    "Show similar failures from the last 30 days",
    "Which ECU caused this timeout?",
    "Is this a known issue?",
]
clicked_example = None
for col, q in zip(example_cols, example_questions):
    if col.button(q, use_container_width=True):
        clicked_example = q

if "history" not in st.session_state:
    st.session_state.history = []

query = st.chat_input("Ask about a test failure, e.g. 'Why did TC_ACC_102 fail?'")
if clicked_example:
    query = clicked_example

for entry in st.session_state.history:
    with st.chat_message("user"):
        st.write(entry["question"])
    with st.chat_message("assistant"):
        st.write(entry["answer"])
        if entry["sources"]:
            with st.expander(f"📎 {len(entry['sources'])} source(s) used"):
                for s in entry["sources"]:
                    st.markdown(
                        f"- **{s['file']}** — `{s['test_case']}` · build `{s['build']}` "
                        f"· verdict **{s['verdict']}** · similarity {s['similarity']}"
                    )

if query:
    with st.chat_message("user"):
        st.write(query)

    with st.chat_message("assistant"):
        with st.spinner("Retrieving similar failures and analyzing root cause..."):
            try:
                collection = rag_engine.get_or_create_collection()
                if collection.count() == 0:
                    st.warning(
                        "No logs indexed yet. Click **'(Re)build index from log "
                        "folder'** in the sidebar first."
                    )
                    answer, sources = None, []
                else:
                    result = rag_engine.answer_failure_query(query)
                    answer, sources = result["answer"], result["sources"]
            except Exception as e:
                st.error(f"Error: {e}")
                answer, sources = None, []

        if answer:
            st.write(answer)
            if sources:
                with st.expander(f"📎 {len(sources)} source(s) used"):
                    for s in sources:
                        st.markdown(
                            f"- **{s['file']}** — `{s['test_case']}` · build `{s['build']}` "
                            f"· verdict **{s['verdict']}** · similarity {s['similarity']}"
                        )
            # --- Jira known issues panel ---
            jira_issues = result.get("jira_issues", [])
            if jira_issues:
                with st.expander(f"🐛 {len(jira_issues)} related Jira issue(s) found", expanded=True):
                    for j in jira_issues:
                        status_color = "🟢" if j["status"] in ("Done", "Closed", "Resolved") else "🔴"
                        st.markdown(
                            f"{status_color} **[{j['key']}]({j['url']})** — {j['summary']}  \n"
                            f"  Status: `{j['status']}`"
                        )
                        if j.get("description"):
                            st.caption(j["description"][:200])
            st.session_state.history.append(
                {"question": query, "answer": answer, "sources": sources, "jira_issues": jira_issues}
            )

st.markdown("---")
with st.expander("📂 Indexed log files (sample data)"):
    chunks = load_logs_from_dir(LOG_DIR)
    for c in chunks:
        verdict_color = "🔴" if c.verdict == "FAIL" else "🟢"
        st.markdown(f"{verdict_color} `{c.source_file}` — {c.test_case} (build {c.build})")
