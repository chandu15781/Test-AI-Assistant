"""
ingest.py
Parses raw test logs (CANoe / HIL / Jenkins style text logs) into structured
chunks suitable for embedding + retrieval.

Designed to be format-tolerant: it does NOT assume a rigid schema, since real
CANoe/CAPL/HIL logs vary a lot between teams. It extracts what it can via
regex and falls back to treating the whole file as one chunk.
"""

import re
import os
import glob
import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LogChunk:
    chunk_id: str
    source_file: str
    test_case: Optional[str]
    build: Optional[str]
    ecu: Optional[str]
    verdict: Optional[str]
    text: str
    metadata: dict = field(default_factory=dict)


HEADER_PATTERNS = {
    "test_case": r"\[Test Case\]\s*([^\n]+)",
    "build": r"\[Build\]\s*([^\n]+)",
    "ecu": r"\[ECU Under Test\]\s*([^\n]+)",
    "jenkins_job": r"\[Jenkins Job\]\s*([^\n]+)",
    "date": r"\[Date\]\s*([^\n]+)",
}

VERDICT_PATTERN = r"Verdict:\s*(PASS|FAIL|INCONCLUSIVE)"
DTC_PATTERN = r"DTC set:\s*([A-Za-z0-9]+)\s*-\s*\"?([^\"\n]+)\"?"
ERROR_CODE_PATTERN = r"Error Code:\s*(0x[0-9A-Fa-f]+)"


def parse_log_file(filepath: str) -> LogChunk:
    """Parse a single log file into one LogChunk (file-level granularity).

    For Phase 1 we keep one chunk per test-case log file, because the whole
    log (stimulus + trace + failure + engineering note) is usually needed
    together to explain *why* something failed. If logs are very long in
    your real environment, switch to sliding-window chunking instead.
    """
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    meta = {}
    for key, pattern in HEADER_PATTERNS.items():
        m = re.search(pattern, text)
        if m:
            meta[key] = m.group(1).strip()

    verdict_match = re.search(VERDICT_PATTERN, text)
    verdict = verdict_match.group(1) if verdict_match else None

    dtc_matches = re.findall(DTC_PATTERN, text)
    error_codes = re.findall(ERROR_CODE_PATTERN, text)

    meta["dtcs"] = [f"{code}: {desc}" for code, desc in dtc_matches]
    meta["error_codes"] = error_codes

    chunk_id = hashlib.md5(filepath.encode()).hexdigest()[:12]

    return LogChunk(
        chunk_id=chunk_id,
        source_file=os.path.basename(filepath),
        test_case=meta.get("test_case"),
        build=meta.get("build"),
        ecu=meta.get("ecu"),
        verdict=verdict,
        text=text,
        metadata=meta,
    )


def load_logs_from_dir(directory: str) -> list[LogChunk]:
    """Load and parse all .log/.txt files in a directory."""
    files = glob.glob(os.path.join(directory, "*.log")) + \
        glob.glob(os.path.join(directory, "*.txt"))
    return [parse_log_file(f) for f in sorted(files)]


def chunk_to_embedding_text(chunk: LogChunk) -> str:
    """Flatten a chunk into the text string actually sent to the embedder.

    We prepend structured metadata as natural-language context so the
    embedding captures test case / build / ECU / verdict, not just raw
    trace lines.
    """
    header_bits = []
    if chunk.test_case:
        header_bits.append(f"Test case: {chunk.test_case}")
    if chunk.build:
        header_bits.append(f"Build: {chunk.build}")
    if chunk.ecu:
        header_bits.append(f"ECU under test: {chunk.ecu}")
    if chunk.verdict:
        header_bits.append(f"Verdict: {chunk.verdict}")
    if chunk.metadata.get("dtcs"):
        header_bits.append("DTCs: " + "; ".join(chunk.metadata["dtcs"]))

    header = " | ".join(header_bits)
    return f"{header}\n\n{chunk.text}"


if __name__ == "__main__":
    sample_dir = os.path.join(os.path.dirname(__file__), "..", "data", "sample_logs")
    chunks = load_logs_from_dir(sample_dir)
    for c in chunks:
        print(f"--- {c.source_file} ---")
        print(f"  test_case={c.test_case} build={c.build} verdict={c.verdict}")
        print(f"  dtcs={c.metadata.get('dtcs')}")
