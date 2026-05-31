# Vesta PM – Maintenance Meld Summaries: Voice Guide

> Reference for `meld_summarizer.py` prompt tuning.
> Generates concise, owner-facing maintenance work order updates.

---

## Purpose

Write a 1-3 sentence maintenance update for a property owner about a single work order. The summary will appear in a monthly maintenance email alongside other work orders for that property.

---

## Core Voice

- **Concise** — 1-3 sentences maximum, no padding
- **Factual** — state what happened or is happening, nothing more
- **Plain-language** — no property management jargon, no technical terms the owner wouldn't know
- **Third person** — do not use "we" or "I"; describe the work neutrally

---

## Tone by Status

| Status | Tone |
|---|---|
| Closed / Completed | Past tense. State what was done, the completion date, and the cost. |
| Could Not Complete | Past tense. Note that the vendor or maintenance team was unable to complete the work. Do NOT call it canceled. |
| Canceled | Past tense. Note that the work order was canceled. |
| Open / In Progress | Present tense. Describe current state. If a scheduled date is provided, mention it. Do not mention cost. |

---

## What to Include

- The nature of the work (what was fixed, serviced, or addressed)
- Vendor name if provided
- Scheduled date for open work orders
- Completion date and cost for closed work orders
- Completion notes or reason for inability to complete, when relevant

---

## What to Exclude

- Internal ticket numbers or meld IDs
- Technical jargon or abbreviations
- Speculative language ("might", "could", "possibly")
- Unnecessary pleasantries or filler

---

## Length Guidelines

| Situation | Target |
|---|---|
| Simple, straightforward work order | 1 sentence |
| Work order with notable context (vendor, scheduling, cost) | 2 sentences |
| Complex or multi-step work order | 3 sentences maximum |
