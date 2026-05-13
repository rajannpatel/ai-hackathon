#!/usr/bin/env python3
"""
KPatch Tracker — Live Kernel Patch Timeline Comparator

Data sources (all public, no authentication required):
  Red Hat  → OSV.dev bulk download  (storage.googleapis.com/osv-vulnerabilities/Red Hat/all.zip)
  Ubuntu   → OSV.dev API            (api.osv.dev — LSN advisory IDs)
  SUSE     → OSV.dev bulk download  (storage.googleapis.com/osv-vulnerabilities/SUSE/all.zip)

Why bulk download for Red Hat and SUSE?
  The OSV.dev /v1/query API requires a specific package name. Red Hat kpatch
  advisories are indexed under version-specific names like
  "kpatch-patch-5_14_0-284_11_1-0" — not "kernel" or "kpatch" — so API
  queries returned nothing. The bulk ZIP contains every advisory as a JSON
  file and lets us filter by summary text directly.

Detection rules:
  Red Hat  → advisory summary contains "kpatch"
  Ubuntu   → advisory ID matches pattern LSN-XXXX-X
  SUSE     → advisory summary contains "live patch"

Output:
    kpatch_results.json  — structured data for the agent to analyse
"""

import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

import requests

# ── Endpoints ─────────────────────────────────────────────────────────────────

OSV_BULK_URL = "https://storage.googleapis.com/osv-vulnerabilities/{ecosystem}/all.zip"

# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class LivePatch:
    id: str
    distro: str
    published: Optional[str]
    summary: str
    cves: list = field(default_factory=list)

@dataclass
class CVETimeline:
    cve_id: str
    patches: dict = field(default_factory=dict)

    def days_between(self, a: str, b: str) -> Optional[int]:
        """Positive = b patched later; negative = b patched earlier."""
        if a not in self.patches or b not in self.patches:
            return None
        pa = self.patches[a].published
        pb = self.patches[b].published
        if not pa or not pb:
            return None
        da = datetime.fromisoformat(pa[:10])
        db = datetime.fromisoformat(pb[:10])
        return (db - da).days

# ── Shared helpers ────────────────────────────────────────────────────────────

def fetch_osv_bulk(ecosystem: str) -> list:
    """
    Download the OSV.dev bulk ZIP for an ecosystem and return all advisories
    as a list of parsed JSON dicts.

    URL pattern:
      https://storage.googleapis.com/osv-vulnerabilities/{ecosystem}/all.zip
    """
    url = OSV_BULK_URL.format(ecosystem=ecosystem.replace(" ", "%20"))
    print(f"    downloading {url.split('/')[-2]} bulk data ...", end=" ", flush=True)

    try:
        resp = requests.get(url, timeout=180)
        resp.raise_for_status()
    except Exception as exc:
        print(f"failed: {exc}")
        return []

    mb = len(resp.content) // 1_048_576
    print(f"{mb}MB, extracting ...", end=" ", flush=True)

    vulns = []
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            for name in zf.namelist():
                if not name.endswith(".json"):
                    continue
                try:
                    vulns.append(json.loads(zf.read(name)))
                except json.JSONDecodeError:
                    continue
    except zipfile.BadZipFile as exc:
        print(f"bad zip: {exc}")
        return []

    print(f"{len(vulns)} advisories")
    return vulns


def cves_from_osv(vuln: dict) -> list:
    """Extract CVE IDs from an OSV record's aliases and details fields."""
    cves = [a for a in vuln.get("aliases", []) if a.startswith("CVE-")]
    cves += re.findall(r"CVE-\d{4}-\d+", vuln.get("details", "") or "")
    return list(set(cves))

# ── Red Hat collector ─────────────────────────────────────────────────────────

def collect_redhat() -> list:
    """
    OSV.dev bulk download for the Red Hat ecosystem.
    Filters advisories whose summary contains "kpatch".
    """
    print("\n  [Red Hat]  via OSV.dev bulk download")
    vulns = fetch_osv_bulk("Red Hat")

    patches = []
    for v in vulns:
        summary = v.get("summary", "")
        if "kpatch" not in summary.lower():
            continue
        patches.append(LivePatch(
            id=v.get("id", ""),
            distro="Red Hat",
            published=v.get("published"),
            summary=summary[:200],
            cves=cves_from_osv(v),
        ))

    print(f"    -> {len(patches)} kpatch advisories found")
    return patches

# ── Ubuntu collector ──────────────────────────────────────────────────────────

def collect_ubuntu() -> list:
    """
    OSV.dev bulk download for the Ubuntu ecosystem.
    Filters advisories whose ID matches the LSN pattern (LSN-XXXX-X).
    Ubuntu live patches use IDs like LSN-0095-1.
    """
    print("\n  [Ubuntu]  via OSV.dev bulk download")
    vulns = fetch_osv_bulk("Ubuntu")

    seen, patches = set(), []
    for v in vulns:
        vid = v.get("id", "")
        if vid in seen or not re.match(r"LSN-\d{4}-\d+", vid):
            continue
        seen.add(vid)
        patches.append(LivePatch(
            id=vid,
            distro="Ubuntu",
            published=v.get("published"),
            summary=v.get("summary", "")[:200],
            cves=cves_from_osv(v),
        ))

    print(f"    -> {len(patches)} LSN advisories found")
    return patches

# ── SUSE collector ────────────────────────────────────────────────────────────

def collect_suse() -> list:
    """
    OSV.dev bulk download for the SUSE ecosystem.
    Filters advisories whose summary contains "live patch".
    """
    print("\n  [SUSE]  via OSV.dev bulk download")
    vulns = fetch_osv_bulk("SUSE")

    patches = []
    for v in vulns:
        summary = v.get("summary", "")
        if "live patch" not in summary.lower():
            continue
        patches.append(LivePatch(
            id=v.get("id", ""),
            distro="SUSE",
            published=v.get("published"),
            summary=summary[:200],
            cves=cves_from_osv(v),
        ))

    print(f"    -> {len(patches)} live patch advisories found")
    return patches

# ── Orchestration ─────────────────────────────────────────────────────────────

def collect_live_patches() -> dict:
    return {
        "Red Hat": collect_redhat(),
        "Ubuntu":  collect_ubuntu(),
        "SUSE":    collect_suse(),
    }

# ── Analysis ──────────────────────────────────────────────────────────────────

def build_timelines(patches: dict) -> dict:
    """Map each CVE to the earliest live patch date per distro."""
    timelines: dict = {}
    for distro, patch_list in patches.items():
        for patch in patch_list:
            for cve in patch.cves:
                if cve not in timelines:
                    timelines[cve] = CVETimeline(cve_id=cve)
                t = timelines[cve]
                if distro not in t.patches or (
                    patch.published and
                    (t.patches[distro].published or "") > patch.published
                ):
                    t.patches[distro] = patch
    return timelines


def compute_stats(timelines: dict) -> dict:
    covered_all = [t for t in timelines.values() if len(t.patches) == 3]
    pairs = [("Red Hat", "Ubuntu"), ("Red Hat", "SUSE"), ("Ubuntu", "SUSE")]
    pairwise = {}

    for a, b in pairs:
        deltas = sorted([
            d for t in covered_all
            if (d := t.days_between(a, b)) is not None
        ])
        if deltas:
            mid = len(deltas) // 2
            pairwise[f"{a} vs {b}"] = {
                "avg_days":    round(sum(deltas) / len(deltas), 1),
                "median_days": deltas[mid],
                "min_days":    deltas[0],
                "max_days":    deltas[-1],
                "sample_size": len(deltas),
                "b_later_pct": round(
                    100 * sum(1 for d in deltas if d > 0) / len(deltas)
                ),
            }

    coverage = {
        d: sum(1 for t in timelines.values() if d in t.patches)
        for d in ["Red Hat", "Ubuntu", "SUSE"]
    }

    ubuntu_gaps = sorted([
        {
            "cve":         c,
            "rh_date":     t.patches["Red Hat"].published,
            "ubuntu_date": t.patches["Ubuntu"].published,
            "lag_days":    t.days_between("Red Hat", "Ubuntu"),
        }
        for c, t in timelines.items()
        if "Red Hat" in t.patches and "Ubuntu" in t.patches
        and (t.days_between("Red Hat", "Ubuntu") or 0) > 30
    ], key=lambda x: x["lag_days"], reverse=True)

    return {
        "total_cves_tracked":  len(timelines),
        "covered_by_all_3":    len(covered_all),
        "coverage_by_distro":  coverage,
        "pairwise_comparison": pairwise,
        "ubuntu_notable_gaps": ubuntu_gaps[:20],
    }

# ── Terminal report ───────────────────────────────────────────────────────────

def print_report(analysis: dict):
    B, R = "\033[1m", "\033[0m"
    colors = {"Red Hat": "\033[91m", "Ubuntu": "\033[93m", "SUSE": "\033[92m"}

    print(f"\n{B}Live Kernel Patch Timeline Report{R}")
    print(f"  Generated      : {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  CVEs tracked   : {analysis['total_cves_tracked']}")
    print(f"  All-3 coverage : {analysis['covered_by_all_3']}")

    print(f"\n{B}Coverage{R}")
    for d, n in analysis["coverage_by_distro"].items():
        c = colors.get(d, "")
        print(f"  {c}{d:<12}{R}  {n:>4} CVEs covered by live patches")

    print(f"\n{B}Pairwise timeline comparison{R}")
    print("  Positive average means the second distro patched later.\n")
    for pair, s in analysis["pairwise_comparison"].items():
        avg = s["avg_days"]
        tag = "ahead" if avg < -7 else ("behind" if avg > 7 else "on par")
        print(f"  {pair}")
        print(f"    avg {avg:+.1f}d | median {s['median_days']:+d}d | "
              f"n={s['sample_size']}  {tag}")

    if analysis["ubuntu_notable_gaps"]:
        print(f"\n{B}Ubuntu notable gaps, more than 30 days behind Red Hat{R}")
        for g in analysis["ubuntu_notable_gaps"][:5]:
            rh = (g["rh_date"] or "")[:10]
            ub = (g["ubuntu_date"] or "")[:10]
            print(f"  {g['cve']}  Red Hat: {rh}  Ubuntu: {ub}  lag: {g['lag_days']} days")

    print(f"\n  Full data saved to kpatch_results.json")

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("KPatch Tracker — Multi-source Collector\n")

    print("Step 1 of 4: Collecting live patches ...")
    patches = collect_live_patches()

    print("\nStep 2 of 4: Cross-referencing CVEs ...")
    timelines = build_timelines(patches)
    print(f"  {len(timelines)} unique CVEs identified")

    print("\nStep 3 of 4: Computing statistics ...")
    analysis = compute_stats(timelines)

    print("\nStep 4 of 4: Writing report ...")
    print_report(analysis)

    output = {
        "generated":         datetime.utcnow().isoformat() + "Z",
        "analysis":          analysis,
        "patches_by_distro": {
            d: [asdict(p) for p in pl]
            for d, pl in patches.items()
        },
    }
    with open("kpatch_results.json", "w") as fh:
        json.dump(output, fh, indent=2)


if __name__ == "__main__":
    main()
