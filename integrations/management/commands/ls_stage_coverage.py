"""
Read-only management command: fetch stage names from LeadSimple for all 13
process types and produce a coverage report against stage_map and MILESTONE_GATES.

Usage:
    python manage.py ls_stage_coverage
"""

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from integrations.leadsimple.client import DEFAULT_BASE_URL
from integrations.leadsimple.services import MILESTONE_GATES, PROCESS_TYPE_MAP
from integrations.leadsimple.stage_map import STAGE_FRAMING


# Process type names for readable output
_PT_NAMES = {
    "1865ad03-f740-4a99-b1a2-e2440430f3f4": "06 Lease Renewals",
    "44ac428c-30c3-4548-8a59-6cc5c687e5f8": "070 Lease Renewals (legacy)",
    "e0dfad31-5459-4578-a0a1-b062180e769b": "08 Move Out",
    "8311a377-6d68-48a0-855d-145fd1380ff4": "09 Move Outs (legacy)",
    "98e21401-58d3-4a6a-b6b0-f22dd11dc831": "Issues",
    "83f4af06-ad96-4ca7-9d21-5a1b00087ac6": "09 Rehab to Turn",
    "864f1bf2-53c8-4135-9196-9f6ecf15cf16": "05 Move Ins",
    "9b99ac28-0775-46cf-8537-12b4b08719b2": "01 Owner Onboarding",
    "7e16b91a-bb9c-447e-937d-db3ce45f9588": "02 Property Onboarding",
    "942fa5d5-6fc6-4495-8c94-bdb311525172": "03 Property Marketing",
    "c9927e92-5c41-4c33-8a9e-8dbd6312eeb6": "04 Applications",
    "75d01712-66f5-44fe-9595-f571e2449896": "07 Late Rent",
    "ee35a777-a854-47d4-82c1-87d022926783": "10 Property Off Boarding",
}


class Command(BaseCommand):
    help = "Fetch LeadSimple process type stages and report coverage against stage_map / MILESTONE_GATES."

    def handle(self, *args, **options):
        api_key = getattr(settings, "LEADSIMPLE_API_KEY", "") or ""
        if not api_key:
            self.stderr.write("LEADSIMPLE_API_KEY not configured.")
            return

        base_url = (
            getattr(settings, "LEADSIMPLE_BASE_URL", "") or DEFAULT_BASE_URL
        ).rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        # Collect all framing keys and milestone-gate strings for comparison
        framing_keys = set(STAGE_FRAMING.keys())  # (uuid, stage_name)
        gate_strings = {}  # uuid → set of stage names
        for uuid, stages in MILESTONE_GATES.items():
            gate_strings[uuid] = set(stages)

        # Track results
        api_stages_by_uuid = {}  # uuid → list of stage name strings
        errors = []

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("LEADSIMPLE STAGE COVERAGE REPORT")
        self.stdout.write("=" * 80)

        for uuid in PROCESS_TYPE_MAP:
            name = _PT_NAMES.get(uuid, uuid)
            url = f"{base_url}/process_types/{uuid}/stages"
            try:
                resp = requests.get(url, headers=headers, timeout=30)
            except requests.RequestException as exc:
                errors.append((uuid, name, str(exc)))
                self.stderr.write(f"\nERROR fetching {name}: {exc}")
                continue

            if resp.status_code != 200:
                errors.append((uuid, name, f"HTTP {resp.status_code}: {resp.text[:200]}"))
                self.stderr.write(
                    f"\nERROR fetching {name}: HTTP {resp.status_code}"
                )
                continue

            data = resp.json()
            # The response shape may be {"data": [...]} or just a list
            stages_list = data if isinstance(data, list) else (data.get("data") or [])
            stage_names = []
            for s in stages_list:
                if isinstance(s, dict):
                    stage_names.append(s.get("name", str(s)))
                else:
                    stage_names.append(str(s))

            api_stages_by_uuid[uuid] = stage_names

            self.stdout.write(f"\n--- {name} ({uuid}) ---")
            self.stdout.write(f"  API stages ({len(stage_names)}):")
            for sn in stage_names:
                self.stdout.write(f"    - {sn}")

        # ── Coverage table ──
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("COVERAGE TABLE: stage_map keys + MILESTONE_GATES vs API")
        self.stdout.write("=" * 80)
        self.stdout.write(
            f"{'Process Type':<35} {'Key String':<45} {'Source':<10} {'Status'}"
        )
        self.stdout.write("-" * 110)

        # Check every framing key
        framing_no_match = []
        for (uuid, stage_name) in sorted(framing_keys, key=lambda k: (_PT_NAMES.get(k[0], k[0]), k[1])):
            pt_name = _PT_NAMES.get(uuid, uuid[:12])
            api_names = api_stages_by_uuid.get(uuid, [])
            if stage_name in api_names:
                status = "MATCH"
            elif uuid not in api_stages_by_uuid:
                status = "NO API DATA"
            else:
                status = "MISMATCH"
                framing_no_match.append((uuid, pt_name, stage_name, api_names))
            self.stdout.write(
                f"  {pt_name:<33} {stage_name:<45} {'framing':<10} {status}"
            )

        # Check every milestone-gate string
        gate_no_match = []
        for uuid, stages in sorted(gate_strings.items(), key=lambda k: _PT_NAMES.get(k[0], k[0])):
            pt_name = _PT_NAMES.get(uuid, uuid[:12])
            api_names = api_stages_by_uuid.get(uuid, [])
            for stage_name in sorted(stages):
                if stage_name in api_names:
                    status = "MATCH"
                elif uuid not in api_stages_by_uuid:
                    status = "NO API DATA"
                else:
                    status = "MISMATCH"
                    gate_no_match.append((uuid, pt_name, stage_name, api_names))
                self.stdout.write(
                    f"  {pt_name:<33} {stage_name:<45} {'gate':<10} {status}"
                )

        # ── Reportable API stages with no framing ──
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("API STAGES WITH NO FRAMING ENTRY (potential gaps)")
        self.stdout.write("=" * 80)
        missing_framing = []
        for uuid, stage_names in sorted(
            api_stages_by_uuid.items(), key=lambda k: _PT_NAMES.get(k[0], k[0])
        ):
            pt_name = _PT_NAMES.get(uuid, uuid[:12])
            for sn in stage_names:
                if (uuid, sn) not in framing_keys:
                    missing_framing.append((uuid, pt_name, sn))
                    self.stdout.write(f"  {pt_name:<35} {sn}")

        # ── Summary ──
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 80)

        if framing_no_match:
            self.stdout.write(
                f"\n(a) FRAMING entries matching NO real API stage: {len(framing_no_match)}"
            )
            for uuid, pt_name, stage_name, api_names in framing_no_match:
                self.stdout.write(f"     {pt_name}: \"{stage_name}\"")
                self.stdout.write(f"       Available API stages: {api_names}")
        else:
            self.stdout.write("\n(a) All framing entries match a real API stage.")

        if gate_no_match:
            self.stdout.write(
                f"\n(b) MILESTONE_GATES entries matching NO real API stage: {len(gate_no_match)}"
            )
            for uuid, pt_name, stage_name, api_names in gate_no_match:
                self.stdout.write(f"     {pt_name}: \"{stage_name}\"")
                self.stdout.write(f"       Available API stages: {api_names}")
        else:
            self.stdout.write("\n(b) All MILESTONE_GATES entries match a real API stage.")

        if missing_framing:
            self.stdout.write(
                f"\n(c) API stages with NO framing entry: {len(missing_framing)}"
            )
            for uuid, pt_name, sn in missing_framing:
                self.stdout.write(f"     {pt_name}: \"{sn}\"")
        else:
            self.stdout.write("\n(c) Every API stage has a framing entry.")

        if errors:
            self.stdout.write(f"\n(d) API errors for {len(errors)} process type(s):")
            for uuid, name, err in errors:
                self.stdout.write(f"     {name}: {err}")

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("END OF REPORT — no code changes made.")
        self.stdout.write("=" * 80 + "\n")
