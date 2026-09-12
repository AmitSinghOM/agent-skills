---
name: source-grounded-claims
description: "Use when producing any substantive answer, claim, assumption, recommendation, or review — research findings, architecture calls, code review, status statements. Attaches an explicit calibrated confidence percentage to each substantive claim (80% is the 'likely true' threshold for acting on it), verifies facts against primary sources before asserting them, and cites only sources actually consulted. Never fabricates a citation."
license: MIT
metadata:
  author: AmitSinghOM
  version: "1.0.0"
---

# Source-Grounded Claims

## Overview

Every substantive claim carries an explicit confidence percentage and, where the
claim is a fact rather than the agent's own reasoning, a cited source the agent
actually consulted. The 80% line is the "likely true" threshold: at or above it
the claim is actionable; below it the claim is explicitly tentative and names
what evidence would raise it.

Being confidently wrong is expensive. This skill exists so a reader can trust a
high number and so an unchecked guess never wears a confidence badge.

## Usage

Apply to:

- Research findings, comparisons, "what is X / how does X work" answers.
- Assumptions and recommendations (architecture, security, tooling, next steps).
- Reviews (code, design, documents) and any status claim ("this passes", "this is done").
- Anything where being confidently wrong is costly.

Do **not** attach confidence to trivial conversational turns (acknowledgements,
clarifying questions). It applies to substantive claims, not chatter.

## Core Concepts

- **Confidence is calibrated, not decorative.** The percentage is the honest
  post-research probability that the statement is true. Unchecked → lower number,
  said out loud. Never inflate to sound sure.
- **80% = the action threshold.** At or above 80% the claim is treated as
  actionable. Below 80% it MUST be marked tentative AND name the evidence that
  would move it above the line.
- **Verify before asserting.** A factual claim MUST be checked against a source
  before it earns a high number. An unverified assertion is a guess wearing a
  confidence badge.
- **Cite what was actually used.** Every fact-based claim names the real source
  consulted — title plus link or absolute path — not a plausible-sounding
  reference. Not opened → not cited.
- **Separate fact from reasoning.** Facts need sources. The agent's own inferences
  need a confidence number but are labelled *inference*, not citation.

## Verification Order

For any factual claim, verify in this order and cite what was used:

1. **The authoritative source for the system in question.** For a tool, library,
   API, or service: its official documentation, specification, changelog, or
   source code. For an organisation's own systems: that organisation's internal
   documentation and code, which are authoritative over anything public.
2. **The artifact itself** for claims about local code, files, or outputs: read
   the actual bytes, run the actual command, and cite the absolute path or the
   command output.
3. **Secondary sources** (blog posts, Q&A sites, general web search) only to
   locate a primary source or when no primary source exists — and say which.

If a source cannot be reached or was not supplied, do not answer the factual
question from memory. Say `unverified`, keep confidence below 80%, name the exact
source or evidence needed, and do not invent a title, link, or path. When a
source excerpt is supplied in the request, treat only that excerpt as verified:
cite its supplied label, separate direct facts from inference, and mark anything
dependent on an unavailable appendix or link as unverified.

## Output Format

Inline, or a short trailing block for multi-claim answers:

```
Inline:  "SARIF 2.1.0 is the version the analyzer emits (~98%, source:
         runs[0] of the file I generated at /tmp/out.sarif)."

Trailing block:
  Confidence (80% = likely-true threshold):
  - <claim> — 92% — <source title> + <link or absolute path>
  - <tentative claim> — 60% — unverified; would rise with <what to check>
  - <inference> — 75% — my reasoning from <cited facts>, not a source
```

Rules:

- Every substantive claim gets a number.
- Every **fact** claim gets a source that was actually consulted.
- Claims **below 80%** are marked tentative AND state what would raise them.
- Never present a source that was not opened; never round a guess up to look certain.

## Worked Example

```
"The spec requires `name` to equal the skill's directory name — 98%
 (read directly: agentskills.io/specification, 'name field').
 Whether this client also enforces it at load time — 55%, unverified;
 would rise to ~90% after loading a deliberately mismatched skill and
 observing the error."
```

## Common Mistakes

- **MUST NOT** state a fact without a consulted source; an uncited fact is
  indistinguishable from a hallucination to the reader.
- **MUST NOT** assign ≥80% to something not verified; the threshold is the
  reader's action signal and inflating it makes it worthless.
- **MUST NOT** cite a source that was not actually opened; a fabricated citation
  is worse than none.
- **SHOULD** prefer the system's own documentation or code over third-party
  summaries; primary sources are authoritative.
- **SHOULD** lower confidence and say "unverified" when a source is unreachable,
  rather than falling back to memory and asserting.
