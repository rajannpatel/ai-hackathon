# KPatch Tracker

A hackathon project that uses an agentic AI workflow to compare live kernel patch timelines across Linux distributions. It answers the question: how far ahead or behind is Ubuntu when it comes to live kernel patching, compared to Red Hat and SUSE?

---

## What it does

The project has two parts that work together:

**kpatch_tracker.py** collects live kernel patch advisories from public security feeds for three distributions, cross-references them by CVE ID, and computes timeline statistics — how many days after Red Hat or SUSE does Ubuntu release a live patch for the same vulnerability.

**agent.py** runs an agentic loop powered by a large language model via OpenRouter. The agent reads its instructions from CLAUDE.md, decides when to run the collector, reads the results, and writes a plain-language report to kpatch_report.md.

The whole workflow runs inside an isolated LXD container on your Ubuntu desktop.

---

## How live patches are detected

Each distribution publishes security advisories in a different format. The collector identifies live patch advisories using these signals:

| Distribution | Data source | Detection signal |
|---|---|---|
| Red Hat | OSV.dev bulk download | Advisory summary contains "kpatch" |
| Ubuntu | OSV.dev bulk download | Advisory ID matches pattern LSN-XXXX-X |
| SUSE | OSV.dev bulk download | Advisory summary contains "live patch" |

All three sources are public and require no authentication.

---

## Project structure

```
kpatch_tracker.py   Data collector — queries OSV.dev and writes kpatch_results.json
agent.py            Agentic runner — tool-calling loop via OpenRouter
CLAUDE.md           Agent system prompt — instructions and report template
setup_lxd.sh        One-time setup script for the LXD container
README.md           This file
```

### Output files (generated at runtime)

```
kpatch_results.json   Raw timeline data — CVEs, patch dates, pairwise statistics
kpatch_report.md      AI-generated analysis report
```

---

## Requirements

- Ubuntu 24.04 or 26.04 Desktop
- LXD (installed by the setup script via Snap)
- An OpenRouter API key — get one at https://openrouter.ai/keys

---

## Setup

Clone or download the project files into a folder, then run the setup script from that folder:

```bash
bash setup_lxd.sh
```

The script will:

1. Install and initialise LXD
2. Create an Ubuntu 24.04 LXD container named `kpatch-tracker`
3. Install Python 3 and create a virtual environment inside the container
4. Copy all project files into the container
5. Prompt for your OpenRouter API key and preferred model, saving both to `~/.config/kpatch-tracker/env`

If you are added to the `lxd` group for the first time, you will need to log out and back in before running the script again.

### Credentials file

Your API key and model choice are stored in:

```
~/.config/kpatch-tracker/env
```

This file has permissions set to 600 and must never be committed to git. It contains:

```
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=google/gemini-3.1-flash-lite
```

To change either value:

```bash
nano ~/.config/kpatch-tracker/env
```

---

## Running the project

### Full agentic workflow

This runs the agent, which collects data and writes the report automatically:

```bash
lxc exec kpatch-tracker \
  --env OPENROUTER_API_KEY="$(grep OPENROUTER_API_KEY ~/.config/kpatch-tracker/env | cut -d= -f2)" \
  --env OPENROUTER_MODEL="$(grep OPENROUTER_MODEL ~/.config/kpatch-tracker/env | cut -d= -f2)" \
  -- bash -c 'cd /workdir && /workdir/venv/bin/python3 agent.py'
```

Data collection takes 5 to 20 minutes. The agent will print each step as it works.

### Collector only, no AI

Run the Python collector directly if you want the raw data without the AI analysis:

```bash
lxc exec kpatch-tracker -- bash -c \
  'cd /workdir && /workdir/venv/bin/python3 kpatch_tracker.py'
```

### Retrieve the report

Copy the finished report from the container to your host machine:

```bash
lxc file pull kpatch-tracker/workdir/kpatch_report.md \
  ~/Projects/ai-hackathon/files/kpatch_report.md
```

---

## How the agentic workflow operates

The agent in `agent.py` runs a tool-calling loop. On each iteration it calls the language model, which decides whether to use one of three tools or stop:

| Tool | What it does |
|---|---|
| `run_collector` | Runs kpatch_tracker.py and returns its output |
| `read_results` | Reads kpatch_results.json and returns the contents |
| `write_report` | Writes the finished markdown report to kpatch_report.md |

The agent reads `CLAUDE.md` as its system prompt on startup. That file defines the workflow order, the report structure, and the analytical questions to answer. You can edit CLAUDE.md to change the agent's behaviour without touching the Python code.

The loop ends when the model responds without requesting any tools, which happens after it has written the report.

---

## What the report covers

- Coverage — how many CVEs each distribution covers with live patches
- Speed comparison — average and median days between distributions for the same CVE
- Notable gaps — CVEs where Ubuntu lagged Red Hat by more than 30 days
- Trend — whether the gap is improving or worsening over time
- Recommendations — practical advice for Ubuntu 24.04 and 26.04 users running live patching

---

## Listing available models

To see which models are available on your OpenRouter account:

```bash
curl -s https://openrouter.ai/api/v1/models \
  --header "Authorization: Bearer $(grep OPENROUTER_API_KEY ~/.config/kpatch-tracker/env | cut -d= -f2)" \
  | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if not m['id'].startswith('~')]"
```
