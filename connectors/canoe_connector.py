"""
canoe_connector.py
CANoe has two realistic integration paths. Use whichever fits your setup:

  A) COM/Automation API (Windows only, CANoe must be installed + licensed
     on the machine running this code). Lets you read live measurement
     state, trigger test modules, and pull XML test reports programmatically.

  B) File-based (works from ANY machine, including Linux servers/containers).
     CANoe/CAPL writes .asc traces, .blf logs, and XML/HTML test reports to
     disk or a network share. This script watches/parses those — this is
     the simpler, more robust path for a server-side RAG pipeline since you
     don't need CANoe installed next to your AI service.

Recommendation: start with (B) for the assistant backend. Use (A) only if
you need the assistant to actively control CANoe (e.g. trigger a re-run).
"""

import os
import glob
import time
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# Path B: file-based ingestion (recommended starting point)
# ---------------------------------------------------------------------------

def parse_canoe_xml_report(xml_path: str) -> dict:
    """CANoe's Test Report Generator can export XML/HTML reports. This parses
    the XML variant into a dict matching the shape ingest.py expects.
    Adjust the tag names below to match your actual CANoe XML report schema
    (it varies by CANoe version and report template)."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # NOTE: these XPath expressions are illustrative - inspect one of your
    # real exported XML reports and adjust tag/attribute names accordingly.
    test_case = root.findtext(".//TestCase/Name", default="UNKNOWN")
    verdict = root.findtext(".//TestCase/Verdict", default="UNKNOWN")
    build = root.findtext(".//Build", default="UNKNOWN")

    failure_steps = []
    for step in root.findall(".//TestStep"):
        if step.findtext("Verdict") == "FAIL":
            failure_steps.append({
                "title": step.findtext("Title", ""),
                "details": step.findtext("Details", ""),
                "timestamp": step.findtext("Timestamp", ""),
            })

    return {
        "test_case": test_case,
        "build": build,
        "verdict": verdict,
        "failure_steps": failure_steps,
    }


def convert_canoe_report_to_log_text(report: dict) -> str:
    """Converts a parsed CANoe XML report into the plain-text log format
    used by app/ingest.py, so it can be dropped straight into your log
    directory and picked up by build_index()."""
    lines = [
        f"[Test Case] {report['test_case']}",
        f"[Build] {report['build']}",
        "",
    ]
    for step in report["failure_steps"]:
        lines.append(f"[{step['timestamp']}] FAIL: {step['title']}")
        lines.append(f"  {step['details']}")
    lines.append(f"Verdict: {report['verdict']}")
    return "\n".join(lines)


def watch_and_export_reports(canoe_report_dir: str, output_log_dir: str, poll_seconds: int = 30):
    """Polls a directory where CANoe drops XML test reports (e.g. a network
    share your CANoe test bench writes to), converts new ones to .log files,
    and writes them to output_log_dir for ingest.py to pick up.

    Run this as a background service (systemd unit / scheduled task) on a
    machine that can see the CANoe report share.
    """
    os.makedirs(output_log_dir, exist_ok=True)
    seen = set()
    print(f"Watching {canoe_report_dir} for new CANoe XML reports...")
    while True:
        for xml_path in glob.glob(os.path.join(canoe_report_dir, "*.xml")):
            if xml_path in seen:
                continue
            try:
                report = parse_canoe_xml_report(xml_path)
                log_text = convert_canoe_report_to_log_text(report)
                out_name = os.path.splitext(os.path.basename(xml_path))[0] + ".log"
                with open(os.path.join(output_log_dir, out_name), "w") as f:
                    f.write(log_text)
                print(f"Converted: {xml_path} -> {out_name}")
            except Exception as e:
                print(f"Failed to parse {xml_path}: {e}")
            seen.add(xml_path)
        time.sleep(poll_seconds)


# ---------------------------------------------------------------------------
# Path A: COM/Automation API (Windows only)
# ---------------------------------------------------------------------------
# Requires: pip install pywin32
# Run this ONLY on Windows, with CANoe installed and licensed locally.
#
# import win32com.client
#
# def connect_to_canoe():
#     app = win32com.client.Dispatch("CANoe.Application")
#     return app
#
# def get_active_measurement_signals(app, signal_names: list[str]) -> dict:
#     measurement = app.Measurement
#     values = {}
#     for name in signal_names:
#         try:
#             signal = app.GetBus("CAN").GetSignal(name)
#             values[name] = signal.Value
#         except Exception as e:
#             values[name] = f"ERROR: {e}"
#     return values
#
# def run_test_module(app, test_module_path: str):
#     test_setup = app.Configuration.TestSetup
#     # Trigger execution - exact API depends on CANoe version, see Vector's
#     # "CANoe COM API" documentation (Help -> Programming -> COM API) for the
#     # exact object model in your installed version.
#     pass


if __name__ == "__main__":
    print(
        "File-based mode: call watch_and_export_reports(canoe_report_dir, "
        "output_log_dir) pointing at your CANoe test-report export folder."
    )
