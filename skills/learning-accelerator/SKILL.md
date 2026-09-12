---
name: learning-accelerator
description: "Use when the user asks to learn, understand, study, master, or prepare for any topic or skill. Turns the request into a structured learning loop — 80/20 plan, difficulty ladder, justified resources, one-page cheat sheet, escalating one-at-a-time quiz, Feynman teach-back — instead of a one-shot explanation. Enforces retrieval practice and spaced retest of logged weak areas."
license: MIT
metadata:
  author: AmitSinghOM
  version: "1.0.0"
---

# Learning Accelerator

## Overview

Converts "help me learn X" into a structured system instead of a wall of text.
Six modes — PLAN, LADDER, RESOURCES, CHEATSHEET, QUIZ, FEYNMAN — used alone on
request or chained as a full loop. Every mode enforces retrieval over re-reading
(TRAP-aligned: Test / Retain / Associate / Perform) and logs weak areas for
spaced retest.

## Usage

Pick the mode from what the user asks. When they just say "teach me X" or "I
need to learn X", run the FULL LOOP: PLAN → LADDER → RESOURCES, then per
session CHEATSHEET → QUIZ → FEYNMAN.

**Mode triggers**

- "learn X quickly / from scratch / in N hours" → PLAN
- "roadmap / path / where do I start" → LADDER
- "what should I read / watch" → RESOURCES
- "summarize / cheat sheet / revision" → CHEATSHEET
- "test me / quiz me / am I ready" → QUIZ
- "do I really understand X" / after any QUIZ round → FEYNMAN

## Core Concepts

### 1. PLAN — 20-hour 80/20 plan

Identify the ~20% of concepts that deliver ~80% of practical results; be
explicit about what is EXCLUDED and why. Break into 10 sessions × 2h (scale the
count to the user's stated time budget), each session = objective + concrete
deliverable + the last 15 minutes reserved for closed-book review of everything
so far. Name the best resource per session.

### 2. LADDER — 5 difficulty levels

Break the topic into 5 levels, beginner → advanced. Each level: milestone
(observable, testable — "can implement LRU from memory", not "understands
caching"), core skills, and an exit test. The user MUST pass the exit test
before advancing — no calendar-based promotion.

### 3. RESOURCES — top 5, justified

Top 5 books/videos/courses/sites for the topic, each with WHY it earns its time
slot and what to skip inside it. Prefer FREE resources; for DSA/interview prep
default to takeuforward.org, labuladong.online/algo, and neetcode.io. Verify
links exist before recommending; NEVER invent titles or URLs.

### 4. CHEATSHEET — one page, 5-minute review

Single page: bullets, one ASCII/mermaid diagram where structure matters, and
one practical example per concept. Hard cap: reviewable in under 5 minutes. For
recurring topics, save it as a file (or artifact) so it persists — a cheat sheet
that scrolls away is not a cheat sheet.

### 5. QUIZ — escalate until it breaks

10 questions, strictly increasing difficulty, ONE AT A TIME — never dump all 10.
After each answer: grade it (correct / partial / wrong), explain the mistake
mechanism, then adapt: wrong → next question probes the same weakness from a
new angle; correct → escalate. Log every miss (date, topic, mistake,
improvement, retest-due) to the user's error log. If the user has configured an
error-log path (for example `prep/error-log.md` in their workspace), append
there and honour its retest rule (default: retest in +3 days; a pattern exits
the log after 2 consecutive clean closed-book solves).

### 6. FEYNMAN — teach it back

Explain the concept in the simplest accurate form (no jargon unless defined
in-line). Then have the user explain it BACK; diagnose gaps in their version
(missing mechanism, wrong causality, memorised phrase without a model); reteach
ONLY the weak points; repeat until their explanation stands alone. The user has
not learned it until they can teach it.

## Rules

- Retrieval beats re-reading: every session ends with closed-book recall, not a
  summary re-read.
- Associate: tie each new concept to something the user already knows or has
  built (one sentence).
- Never mark a level or session complete on time spent — only on a passed exit
  test.
- Interview-prep topics inherit stricter rules automatically: time-boxed
  Perform conditions matching real loops, same-day error logging, free
  resources first.
- Never fabricate resources, links, or "commonly asked" questions. If a
  resource cannot be verified, say so and offer a verified alternative.

## Example

User: "I need to learn Kafka quickly."

→ PLAN: 10 × 2h sessions on the 20% core (log/partition model, producer acks,
consumer groups + rebalancing, offsets/exactly-once, retention/compaction);
EXCLUDED: Streams DSL internals, Connect plugins (state why). Session 1
deliverable: local single-broker + CLI produce/consume; last 15 min closed-book:
draw the partition/offset model from memory.

→ QUIZ q1: "What does `acks=all` guarantee, and what can still be lost?" …
escalating to q10: "Design the consumer-group strategy for strict per-key
ordering at 100K msg/s with rolling deploys."

→ FEYNMAN: "Explain why a consumer group can never have more active consumers
than partitions — as if to a new grad."
