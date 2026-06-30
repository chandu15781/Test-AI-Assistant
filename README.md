# Test Failure Analysis Assistant (Phase 1 MVP)

An open-source Generative AI assistant that explains *why* a test failed,
using your CANoe/HIL/Jenkins logs as its knowledge base — instead of making
you read raw error codes and CAN traces.

This is Phase 1 of the roadmap (Failure Analysis Assistant). It's built so
Phase 2 (Test Knowledge / RAG over Polarion requirements) and later phases
can reuse the same `rag_engine.py` pattern with a second collection.

---

## 1. Stack (Groq API + local embeddings)

| Component        | Choice                          | Why |
|-------------------|----------------------------------|-----|
| LLM                | **Groq API** (mixtral-8x7b) | Fast, reliable cloud API with rate limits; get key at https://console.groq.com |
| Embeddings         | **sentence-transformers** (`all-MiniLM-L6-v2`) | Small, fast, runs on CPU, no API calls |
| Vector DB          | **ChromaDB**                    | Embedded (no server to run), persists to disk, easy to swap for Qdrant/pgvector later if you outgrow it |
| Orchestration      | Plain Python (no LangChain needed at this scale) | Fewer moving parts to debug; you can introduce LangChain/LlamaIndex later if the pipeline grows multi-step agents |
| UI                 | **Streamlit**                   | Fastest path to a usable internal tool for engineers |

If you ever want a different LLM backend, the only function you'd touch is `call_groq()` in `app/rag_engine.py` —
everything else (retrieval, prompt construction, UI) stays the same.

---

## 2. Project structure

```
ai-test-assistant/
├── app/
│   ├── ingest.py          # Parses raw logs into structured chunks
│   ├── rag_engine.py       # Embeddings + ChromaDB + Groq pipeline
│   └── streamlit_app.py    # UI
├── connectors/
│   ├── jenkins_connector.py
│   ├── jira_connector.py
│   ├── canoe_connector.py
│   └── polarion_connector.py
├── data/sample_logs/       # 4 sample CANoe/HIL logs to try it out immediately
├── vectorstore/             # ChromaDB's on-disk index (created automatically)
├── requirements.txt
└── .env.example
```

---

## 3. Quick start (sample data, ~5 minutes)

```bash
# 1. Get a Groq API key (free): https://console.groq.com/
#    Copy it to .env: GROQ_API_KEY=your_key_here

# 2. Install dependencies
pip install -r requirements.txt

# 3. Launch the UI
cd app
streamlit run streamlit_app.py
```

Open the URL Streamlit prints (usually `http://localhost:8501`), click
**"(Re)build index from log folder"** in the sidebar, then ask:
*"Why did TC_ACC_102 fail?"*

The 4 included sample logs are designed so this question actually
demonstrates the value: two builds of the same test failing with the same
radar bus-off pattern, plus two unrelated logs (one pass, one different
failure) so you can see the retrieval correctly ignores irrelevant ones.

---

## 4. Connecting to your real tools

The assistant currently reads from `data/sample_logs/`. To go live, you
have two jobs for each tool: (1) **get logs/data out of the tool and into
plain-text files** ingest.py can parse, and (2) **point the indexer at that
folder**. Below is exactly how for each system in your stack.

### 4.1 CANoe / CAPL / HIL logs

**Recommended path — file-based** (works without installing CANoe next to
your AI server):

1. Configure CANoe's Test Report Generator to export XML or HTML reports
   to a shared folder (or have your HIL bench script copy `.asc`/`.blf`
   trace exports + test reports there after each run).
2. Run the included watcher, which converts new CANoe XML reports into the
   plain-text log format `ingest.py` expects:

   ```bash
   python connectors/canoe_connector.py
   # then call: watch_and_export_reports(canoe_report_dir, output_log_dir)
   ```

   **Important:** the XML tag names in `parse_canoe_xml_report()` are
   illustrative placeholders. Open one of your actual exported CANoe XML
   reports, check the real tag/attribute names (these vary by CANoe
   version and report template), and adjust the `findtext()` calls to
   match.
3. Point `build_index()` at `output_log_dir` instead of `data/sample_logs`.

**Alternative — live COM API** (Windows only, CANoe installed + licensed
on that machine): use this only if you want the assistant to *trigger*
test runs or read *live* signal values, not just analyze completed logs.
A skeleton is in the commented-out section at the bottom of
`canoe_connector.py` — you'll need `pip install pywin32` and to consult
Vector's CANoe COM API docs (Help → Programming → COM API in CANoe) for
the exact object model in your installed version, since this differs
between CANoe releases.

### 4.2 Jenkins

1. Generate a Jenkins API token: your user icon → Configure → API Token.
2. Set `JENKINS_URL`, `JENKINS_USER`, `JENKINS_API_TOKEN` in `.env`.
3. Install the client: `pip install python-jenkins`.
4. Pull a build's console log into the ingestible log folder:

   ```python
   from connectors.jenkins_connector import save_console_logs_for_ingestion
   save_console_logs_for_ingestion("nightly_hil_regression", "data/jenkins_logs")
   ```
5. Schedule this (cron, Jenkins post-build step, or a small polling
   script) to run after every relevant job completes, then re-run
   `build_index()` on the combined log directory.

   **Tip:** if your pipeline publishes structured JUnit/xUnit reports,
   `fetch_test_report()` gives you pass/fail counts per test directly —
   useful later for Phase 5 (Intelligent Test Reporting) without needing
   the LLM to parse console text at all.

### 4.3 Jira

1. Generate an API token at id.atlassian.com → Security → API tokens.
2. Set `JIRA_URL`, `JIRA_USER_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`.
3. Install: `pip install jira`.
4. This connector is used two ways:
   - **"Is this a known issue?"** — `search_known_issues("ACC_ACTIVATION_TIMEOUT")`
     searches Jira for tickets matching an error signature, and you can
     splice the results into the RAG context in `rag_engine.answer_failure_query()`.
   - **Auto-filing defects** — `create_defect_from_failure(...)` files a new
     ticket from the AI's root cause summary. **Start with a human-review
     step before this writes to Jira** (e.g. show the draft in Streamlit
     with a "File this defect" confirm button) rather than auto-filing
     silently — false positives in an automated defect tracker erode trust
     fast.

### 4.4 Polarion (Phase 2 — requirements / test specs)

1. Generate a Personal Access Token in Polarion (User Settings → Access
   Tokens). Requires Polarion 21+ for the native REST API used here; older
   versions need the SOAP/OSLC API instead (see note in
   `polarion_connector.py`).
2. Set `POLARION_URL`, `POLARION_TOKEN`, `POLARION_PROJECT_ID`.
3. Pull requirements + linked test cases into a second ingestible folder:

   ```python
   from connectors.polarion_connector import export_requirements_for_rag
   export_requirements_for_rag("Adaptive Cruise Control", "data/polarion_docs")
   ```
4. Build a **separate** ChromaDB collection for this (e.g.
   `requirements_knowledge` vs `test_failure_logs`) so failure-log queries
   and requirements queries don't dilute each other's retrieval. In
   `rag_engine.py`, that just means calling `get_or_create_collection()`
   with a different `COLLECTION_NAME`.

### 4.5 IBM DOORS / GitLab / vehicle logs (later phases)

Not built yet in this MVP, but the pattern is identical every time:
**export to plain text or structured XML/JSON → write a small parser that
maps it into the same `LogChunk`-like shape `ingest.py` uses → call
`build_index()`**. DOORS has an OSLC API similar to Polarion's; GitLab has
a clean REST API for CI job logs, very similar to the Jenkins connector
above.

---

## 5. Going to production: a few things to decide before rollout

- **Where does Ollama run?** A single shared GPU box on your internal
  network is the common setup — point every engineer's Streamlit instance
  at the same `OLLAMA_HOST`. Don't run a separate Ollama per laptop unless
  hardware is the constraint.
- **Re-indexing cadence.** Don't rebuild the whole index on every query —
  run `build_index()` on a schedule (e.g. every 15 min via cron, or
  triggered by a Jenkins post-build webhook) so new failures become
  searchable shortly after they happen.
- **Chunking strategy at scale.** Right now each log file is one chunk
  (works fine for the test-case-sized logs in this MVP). If your real
  HIL logs run into the tens of thousands of lines, switch to
  sliding-window chunking inside `ingest.py` so retrieval doesn't return
  one giant, mostly-irrelevant chunk.
- **Access control.** If logs contain anything sensitive, put the
  Streamlit app behind your normal SSO/reverse proxy — Streamlit itself
  has no built-in auth.

---

## 6. What's intentionally NOT in this MVP yet

Per the original roadmap, this covers Phase 1 only. Phases 2–5
(requirements RAG, CAPL generation, test automation code generation,
executive reporting) follow the same architecture — ask if you'd like any
of those scaffolded next.
