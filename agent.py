#!/usr/bin/env python3
"""
KPatch Agent — Agentic analysis runner using OpenRouter
Runs a tool-calling loop: the AI decides when to collect data,
read results, and write the final report.

Usage (credentials stored in ~/.config/kpatch-tracker/env):
    lxc exec kpatch-tracker \\
      --env OPENROUTER_API_KEY="$(grep OPENROUTER_API_KEY ~/.config/kpatch-tracker/env | cut -d= -f2)" \\
      --env OPENROUTER_MODEL="$(grep OPENROUTER_MODEL ~/.config/kpatch-tracker/env | cut -d= -f2)" \\
      -- bash -c 'cd /workdir && /workdir/venv/bin/python3 agent.py'

To override the model for one run:
    --env OPENROUTER_MODEL=anthropic/claude-haiku-4.5
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────────────

MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
    default_headers={
        "HTTP-Referer": "https://github.com/kpatch-tracker",
        "X-Title":      "KPatch Tracker",
    },
)

# ── Tool definitions (what the AI can call) ───────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_collector",
            "description": (
                "Run kpatch_tracker.py to collect live kernel patch advisories "
                "from OSV.dev for Red Hat, SUSE, and Ubuntu. "
                "Takes 5–20 minutes. Must be called before read_results."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_results",
            "description": (
                "Read kpatch_results.json and return its contents for analysis. "
                "Only call this after run_collector has finished."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_report",
            "description": "Write the finished analysis report to kpatch_report.md.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type":        "string",
                        "description": "Full markdown content of the report.",
                    }
                },
                "required": ["content"],
            },
        },
    },
]

# ── Tool implementations ──────────────────────────────────────────────────────

def run_collector(_args: dict) -> str:
    print("  [tool] running kpatch_tracker.py …")
    result = subprocess.run(
        [sys.executable, "kpatch_tracker.py"],
        capture_output=True, text=True,
    )
    output = result.stdout
    if result.returncode != 0:
        output += f"\nSTDERR:\n{result.stderr}"
    return output or "(no output)"


def read_results(_args: dict) -> str:
    path = Path("kpatch_results.json")
    if not path.exists():
        return "Error: kpatch_results.json not found. Call run_collector first."
    text = path.read_text()
    # Truncate if enormous — keep the analysis section in full
    if len(text) > 12_000:
        data = json.loads(text)
        trimmed = {
            "generated": data.get("generated"),
            "analysis":  data.get("analysis"),
        }
        return json.dumps(trimmed, indent=2)
    return text


def write_report(args: dict) -> str:
    content = args.get("content", "")
    Path("kpatch_report.md").write_text(content)
    return "✓ kpatch_report.md written successfully."


DISPATCH = {
    "run_collector": run_collector,
    "read_results":  read_results,
    "write_report":  write_report,
}

# ── Agent loop ────────────────────────────────────────────────────────────────

def load_system_prompt() -> str:
    p = Path("CLAUDE.md")
    return p.read_text() if p.exists() else "You are a security analyst."


def run_agent():
    print(f"KPatch Agent — OpenRouter")
    print(f"Model: {MODEL}\n")

    messages = [
        {"role": "system",  "content": load_system_prompt()},
        {"role": "user",    "content": "Begin the KPatch Tracker analysis workflow."},
    ]

    step = 0
    while True:
        step += 1
        print(f"\n[step {step}] Calling {MODEL} ...")

        response = client.chat.completions.create(
            model=MODEL,
            tools=TOOLS,
            messages=messages,
            max_tokens=4096,
        )
        msg = response.choices[0].message
        # Append assistant turn (convert to dict for history)
        messages.append(msg.model_dump(exclude_unset=False))

        if msg.content:
            print(f"Agent: {msg.content}\n")

        # No tool calls → agent is done
        if not msg.tool_calls:
            break

        # Execute each requested tool and feed results back
        for call in msg.tool_calls:
            fn_name = call.function.name
            fn_args = json.loads(call.function.arguments or "{}")

            print(f"Tool: {fn_name}")
            result = DISPATCH[fn_name](fn_args)
            print(f"  done")

            messages.append({
                "role":         "tool",
                "tool_call_id": call.id,
                "content":      result[:10_000],   # guard against huge payloads
            })

    print("\nAgent finished.")
    if Path("kpatch_report.md").exists():
        print("  Report saved to kpatch_report.md")


if __name__ == "__main__":
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("Error: OPENROUTER_API_KEY environment variable is not set.")
        print("  export OPENROUTER_API_KEY=sk-or-...")
        raise SystemExit(1)
    run_agent()
