"""
rag_engine.py
Open-source RAG pipeline for the Failure Analysis Assistant.

Stack:
- Embeddings: sentence-transformers (all-MiniLM-L6-v2) - runs locally, free
- Vector store: ChromaDB - embedded, no server needed, persists to disk
- LLM: Ollama (e.g. llama3.1, mistral) - runs locally, no API key needed

This file intentionally has NO dependency on OpenAI/Azure. Swap the
LLM section for a hosted API later if you ever want to (see notes at bottom).
"""

import os
import sys
import requests
import chromadb
from chromadb.utils import embedding_functions
from groq import Groq
from dotenv import load_dotenv

from ingest import load_logs_from_dir, chunk_to_embedding_text

# Add connectors directory to path for Jira integration
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "connectors"))

# Load .env file if it exists
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

CHROMA_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "vectorstore", "chroma_db")
COLLECTION_NAME = "test_failure_logs"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"  # Recommended Groq model (mixtral-8x7b-32768 deprecated)


def get_chroma_client():
    return chromadb.PersistentClient(path=CHROMA_DB_PATH)


def get_embedding_function():
    # Runs locally via sentence-transformers, downloaded once from HF hub.
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )


def get_or_create_collection():
    client = get_chroma_client()
    ef = get_embedding_function()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


def _jira_configured() -> bool:
    """Return True if all required Jira env vars are present."""
    return all(os.environ.get(v) for v in ["JIRA_URL", "JIRA_USER_EMAIL", "JIRA_API_TOKEN"])


def build_index(log_dir: str):
    """Parse all logs in log_dir and (re)build the vector index.
    Also ingests Jira issues into the same ChromaDB collection if configured.
    """
    collection = get_or_create_collection()
    chunks = load_logs_from_dir(log_dir)

    ids, documents, metadatas = [], [], []

    # --- Local log files ---
    for c in chunks:
        ids.append(c.chunk_id)
        documents.append(chunk_to_embedding_text(c))
        metadatas.append({
            "source_file": c.source_file,
            "test_case": c.test_case or "unknown",
            "build": c.build or "unknown",
            "ecu": c.ecu or "unknown",
            "verdict": c.verdict or "unknown",
        })

    # --- Jira issues ---
    jira_count = 0
    if _jira_configured():
        try:
            from jira_connector import fetch_issues_as_chunks
            jira_chunks = fetch_issues_as_chunks(max_results=50)
            for jc in jira_chunks:
                ids.append(jc["chunk_id"])
                documents.append(jc["document"])
                metadatas.append(jc["metadata"])
            jira_count = len(jira_chunks)
            print(f"[Jira] Fetched {jira_count} issues into index.")
        except Exception as e:
            print(f"[Jira] Skipping Jira indexing: {e}")

    if not ids:
        return 0

    # upsert = safe to re-run when logs/issues are added/updated
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    return len(chunks), jira_count


def retrieve_similar_failures(query: str, n_results: int = 4) -> list[dict]:
    """Return the top-N most similar log chunks to the query."""
    collection = get_or_create_collection()
    if collection.count() == 0:
        return []

    n_results = min(n_results, collection.count())
    results = collection.query(query_texts=[query], n_results=n_results)

    output = []
    for doc, meta, dist in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        output.append({"text": doc, "metadata": meta, "distance": dist})
    return output


def call_groq(prompt: str, system: str = "") -> str:
    """Call Groq API for LLM inference. Requires GROQ_API_KEY env variable."""
    if not GROQ_API_KEY:
        return (
            "⚠️ GROQ_API_KEY not set. Please set the GROQ_API_KEY environment variable "
            "with your Groq API key. Get one at https://console.groq.com/"
        )
    try:
        client = Groq(api_key=GROQ_API_KEY)
        message = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            model=GROQ_MODEL,
            temperature=0.2,
            max_tokens=1024,
        )
        return message.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Groq API call failed: {e}"


ROOT_CAUSE_SYSTEM_PROMPT = """You are a senior automotive test engineer assistant.
You analyze CANoe/HIL/CAPL test logs and explain WHY a test failed, in the style
a release engineer would write in a defect ticket: short, technical, causal.

Rules:
- Lead with the most likely root cause in one sentence.
- Reference the specific signal/CAN ID/ECU involved if present in the context.
- If similar past failures are present in the context, explicitly say so and
  name the build/JIRA ticket if available.
- If the context does not support a confident root cause, say what additional
  data would be needed instead of guessing.
- Keep the answer under 120 words. No filler, no disclaimers.
"""


def answer_failure_query(user_question: str, n_results: int = 4) -> dict:
    """Full Phase-1 pipeline: retrieve -> build context -> LLM -> structured answer.
    Also searches Jira for known matching issues if configured.
    """
    retrieved = retrieve_similar_failures(user_question, n_results=n_results)

    if not retrieved:
        return {
            "answer": "No indexed logs yet. Ingest some test logs first.",
            "sources": [],
            "jira_issues": [],
        }

    context_blocks = []
    for i, r in enumerate(retrieved):
        context_blocks.append(
            f"[Source {i+1}: {r['metadata']['source_file']} | "
            f"test_case={r['metadata']['test_case']} | build={r['metadata']['build']} | "
            f"verdict={r['metadata']['verdict']}]\n{r['text']}"
        )
    context = "\n\n---\n\n".join(context_blocks)

    # --- Search Jira for known issues matching the query ---
    jira_issues = []
    if _jira_configured():
        try:
            from jira_connector import search_known_issues
            # Use the first 6 words of the question as the search signature
            signature = " ".join(user_question.split()[:6])
            jira_issues = search_known_issues(signature, max_results=3)
        except Exception as e:
            print(f"[Jira] Known-issue search failed: {e}")

    # Append Jira context to the LLM prompt if any tickets were found
    jira_context = ""
    if jira_issues:
        jira_lines = []
        for j in jira_issues:
            jira_lines.append(
                f"  - {j['key']} [{j['status']}]: {j['summary']}\n"
                f"    URL: {j['url']}\n"
                f"    Description snippet: {j['description'][:200]}"
            )
        jira_context = "\n\nRelated known Jira issues:\n" + "\n".join(jira_lines)

    prompt = f"""Context (retrieved test logs, most relevant first):

{context}{jira_context}

---

Engineer question: {user_question}

Give the root cause analysis now."""

    answer = call_groq(prompt, system=ROOT_CAUSE_SYSTEM_PROMPT)

    return {
        "answer": answer,
        "sources": [
            {
                "file": r["metadata"]["source_file"],
                "test_case": r["metadata"]["test_case"],
                "build": r["metadata"]["build"],
                "verdict": r["metadata"]["verdict"],
                "similarity": round(1 - r["distance"], 3),
            }
            for r in retrieved
        ],
        "jira_issues": jira_issues,
    }


# ---------------------------------------------------------------------------
# NOTE on swapping LLM backends later:
# If you ever want a different LLM provider, the only function that
# needs to change is call_groq(). Keep the same signature
# (prompt, system) -> str and the rest of the pipeline is untouched.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log_dir = os.path.join(os.path.dirname(__file__), "..", "data", "sample_logs")
    n = build_index(log_dir)
    print(f"Indexed {n} log chunks.")
    result = answer_failure_query("Why did TC_ACC_102 fail?")
    print(result["answer"])
    print(result["sources"])
