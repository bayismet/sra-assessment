# SRA Assessment

The SRA is closing more tickets than ever while getting more of them wrong, and the offline eval that should catch this has been testing stale scenarios for five months. Before this system can be extended, it needs to be accurately measured, and before it can be accurately measured, the data feeding its eval needs to reflect the product as it actually exists today.

I gave the deepest treatment to **Diagnose** and **Make It Measurable** because the system's most urgent problem is that its operators cannot see how badly it is degrading. **Own It** gets medium depth to demonstrate operational readiness. **Extend** is handled lightest, deliberately — extending a system with a stale eval suite and outdated knowledge base is the wrong first move, and saying so is part of the answer.

---

## Diagnose: The System Is Degrading Behind Good-Looking Numbers

### Two dashboards, two stories

The metrics that leadership reports to the executive team all look healthy: 61% automation rate, 0.3-hour median response time, shrinking queue depth. The quality metrics that only manual sampling catches are all declining:

| Metric | Launch | Current | Trend |
|---|---|---|---|
| Production correctness (manual sampling) | 90% | 84% | Steady decline every month |
| Reopen rate | 7% | 13% | Nearly doubled |
| Error/troubleshooting accuracy | 86% | 71% | 15-point collapse |
| Tickets closed without human | 58% | 61% | Up |

The system is automating more while getting more wrong. This is the **automation-confidence trap**: the dashboard says things are fine precisely because the system is confidently resolving tickets it should be escalating.

The category breakdown identifies where the damage is concentrated:

| Category | Launch | Current | Volume |
|---|---|---|---|
| Informational | 93% | 94% | 34% |
| Configuration | 89% | 88% | 27% |
| Error / troubleshooting | 86% | 71% | 21% |
| Access requests | -- | -- | 18% |

Informational and configuration tickets are flat. The entire decline is driven by error/troubleshooting. Access requests — 18% of volume — have never been measured.

### Root cause: stale product knowledge

The product shipped v14 ten weeks ago with changes to the permissions model, the approval workflow engine, and several error codes. The product knowledge artifact was last refreshed four months ago. The scheduled refresh job has been failing silently — confirmed by a TODO comment left by the departed engineer at the bottom of `sra_runtime.py`.

The agent is answering v14 questions using v13 documentation. Error/troubleshooting is the category most sensitive to version-specific accuracy — error codes, permission behaviors, and workflow rules are precisely the things that changed in v14. A customer reports an error code that was renamed; the agent retrieves the old documentation; it gives a confidently wrong answer; the ticket auto-closes; and the customer reopens it days later. The doubling of the reopen rate from 7% to 13% is the customer-facing consequence.

This is not a model problem. It is a data freshness problem.

### Context stuffing

`load_context()` in `sra_runtime.py` fetches the entire product knowledge base — roughly 180 document chunks — and injects all of it into the message history. This is called on every step of the agentic loop (up to 8 steps per ticket), potentially loading 1,440 chunks per ticket.

This is entirely redundant. The agent already has a `search_product_docs` tool that performs targeted retrieval. The full context load wastes tokens, inflates cost, and dilutes the model's attention with irrelevant material. At 2,400 tickets per month the cost is manageable. At the planned 31,000 tickets per month it becomes a blocker.

### Why the eval did not catch any of this

The original eval harness was a 52-line script. It used the same model (`frontier-model-v2`) as both agent and judge — the model scored its own output. It had a single binary PASS/FAIL judgment with no rubric, no per-category breakdown, no regression detection, and no CI gating. It also had a data flow bug: the original `finish()` never returned the response body, so the judge evaluated empty strings against reference answers for five months.

I rewrote the harness to fix these structural problems: independent judge model, three-dimension rubric with weighted composites, per-category stratification, regression detection against a saved baseline, CI exit code on regression, and a staleness gate that warns when the test suite lacks cases for the current product version. But even a well-built harness cannot catch production drift if its test data is stale.

The test suite contains 240 cases written at launch, all targeting v13 product behavior. It has never been updated. There are zero cases testing v14 scenarios and zero cases for access requests. The eval reports 91% because it is testing the agent on five-month-old questions against five-month-old documentation — scenarios the agent still handles correctly. It cannot detect the error/troubleshooting collapse because it does not test the scenarios that are failing in production.

Good tooling without a maintenance process produced the same result as no tooling at all.

---

## Make It Measurable: Fixing the Feedback Loop

This is where I spent the most depth. A support agent without a working feedback loop will silently degrade — which is exactly what happened. Every other improvement depends on being able to measure whether it worked.

### Eval data flow fix (code)

A small change to `finish()` in `sra_runtime.py`: always include `action` and `outcome` in the return dict. For escalation cases, `body` remains None but the action and escalation reason are present. This lets the judge evaluate whether the agent escalated at the right point and for the right reason, not just whether it produced a reply.

### Test suite updates (code)

The `EvalCase` dataclass includes `product_version` and `source` fields to support version-aware evaluation, but the existing 240 cases are all v13/launch_set. Changes:

- Add v14 cases for error/troubleshooting and configuration, sourced from the production quality samples that human reviewers have already graded. Ground truth already exists — these are the cheapest high-value cases to add.
- Add access request cases. 18% of production volume, zero eval coverage.
- Tag new cases as `source: "production_sample"` to distinguish production-derived coverage from launch-era cases.
- Add a staleness gate: the harness warns when no eval cases exist for the current product version, preventing the test suite from silently falling behind the product again.

### Stratified reporting (code)

The harness already computes per-category composites and detects per-category regressions. What it lacks is visibility — the headline report buries category data below the aggregate pass rate. Changes:

- Category-level scores appear alongside the aggregate in the headline report. A 15-point collapse in one category can no longer hide behind stable scores in others.
- Categories with fewer than `MIN_CATEGORY_SIZE` cases are flagged in the final report (the check already exists in `load_cases` — I surface it where operators see it).

### Production monitoring (new code — `sra_monitor.py`)

The offline eval and production quality sampling are disconnected. The eval tests a frozen case set; production sampling catches real drift but has no automation. `sra_monitor.py` bridges the gap with four checks that run between eval cycles:

- **Confidence distribution.** Alerts when scores cluster near the 0.7 decision threshold — a leading indicator that the agent is becoming uncertain.
- **Reopen rate by category.** Alerts when any category exceeds twice its baseline rate. The overall doubling from 7% to 13% would have triggered this within weeks, not months.
- **Cost per ticket.** Reports mean, p95, and max. Addresses the cost tracking finance has requested and catches regressions from context stuffing or runaway loops.
- **KB freshness.** Alerts if the knowledge base has not been refreshed within seven days. The four-month-stale KB would have been caught in week one.

The offline eval tells you the agent can handle known cases under controlled conditions. Production monitoring tells you the agent is handling real cases well in the wild. You need both because they catch different failure modes. The original system had one that was non-functional and one that did not exist.

---

## Extend: Access Requests — After the Foundation Is Solid

I gave this section the lightest treatment deliberately. Extending a system with a stale eval suite and outdated knowledge base is operationally irresponsible. The eval cannot tell you whether the extension works. The KB may feed the extension wrong information. This is prioritization, not avoidance.

When the foundation is solid, access requests are the right next extension — 18% of volume with no dedicated logic. Under the read-only constraint, SRA cannot provision access directly, but it can:

1. Classify the request and validate it against the customer's entitlements.
2. Identify the correct admin contact from the CRM.
3. Draft a templated response directing the customer to their admin.
4. Track whether the request was fulfilled.

There is a data quality constraint. The admin contact list has a median staleness of 7 months, and 22% of accounts have not been updated in over a year. The SRA should not route customers to a contact that may no longer be valid. The implementation includes a staleness check: if the admin record exceeds a configurable threshold, the SRA escalates instead of replying with a potentially stale contact.

**Readiness gate:** Eval harness producing real scores. KB current. Error/troubleshooting stabilized above 85%. Production monitoring operational for at least two weeks with no alerts.

---

## Own It: Week-One Triage and Operational Posture

### Week-one actions

1. **Fix the KB refresh job.** Highest-ROI single action. Restores v14 documentation and should immediately improve the error/troubleshooting category.
2. **Deploy the runtime fix.** The `finish()` change makes response data observable in the eval, and the context stuffing removal (already implemented in the submitted code) eliminates the redundant full-doc load. Run the eval before and after to verify no regression.
3. **Deploy production monitoring.** Start collecting confidence distributions, reopen rates by category, cost data, and KB freshness. These signals begin accumulating immediately.
4. **Run the updated eval and save a baseline.** The first honest score will be lower than 91%. That is the point — it will be real.

### Ongoing practices

- Eval cases updated with every product release — v14 cases added immediately.
- Weekly category-level metric review, not just aggregate pass rate.
- Monitor alerts routed to on-call with defined response playbooks.
- Monthly: sample 20 failed cases for eval suite expansion, prioritizing changed product areas.
- Quarterly: recalibrate judge rubric anchors against a human-labeled gold set.

### Scale readiness

The expansion to 31,000 tickets per month across three more portfolio companies cannot proceed until measurement is trustworthy and context stuffing is resolved. At the current p95 cost of $2.10 per ticket, expansion would cost roughly $65,000 per month at the high end. Removing context stuffing should compress this substantially, but per-ticket cost tracking is a prerequisite for making the scaling decision with real numbers.

More importantly, each portfolio company has different products and customer profiles. Quality monitoring must support per-tenant stratification so a collapse at one company does not hide behind aggregate scores — the same masking problem already present in the current single-tenant deployment.

---

## Depth trade-offs

I went deepest on diagnosis and measurement because they are the leverage points. If you cannot see the problem, you cannot fix it. If you cannot measure whether your fix worked, you are guessing. I went lightest on extension because shipping new features into a system with broken observability is the wrong sequence. That ordering reflects what I would actually prioritize in week one.

---

## AI Usage Disclosure

**Tools used.** Claude (Opus) via Claude Code for code analysis, code generation, and prose drafting.

**What I delegated and what I kept.** I delegated code drafting — the eval harness improvements, the production monitor, and the runtime fix — after designing the approach, selecting the rubric dimensions, and setting the weights. I also delegated prose drafting and structural suggestions for this document. I kept the diagnosis: reading the runtime code, tracing the data flow from `run()` through `finish()` to the eval harness, and identifying that the eval's real failure is operational (stale test suite, no update process) rather than purely a code bug. I kept the root cause analysis connecting the stale KB to the error/troubleshooting collapse. I kept the depth allocation strategy and the judgment that extending before fixing measurement is wrong.

**Where AI got something wrong.** Initial analysis correctly identified that the original `finish()` never returned a response body. I initially treated this as the primary eval bug. On deeper analysis, the more critical problem is the stale test suite — even with the data flow fix, an eval testing only v13 scenarios would still report healthy scores. The code fix was necessary but insufficient; the operational failure was the root cause. The generated code also initially added unnecessary error handling in paths that could not fail, which I removed.

**Which conclusions are my own judgment.** The depth trade-off, the root cause attribution (data freshness, not model capability), the framing of the automation-confidence trap, the assessment that the eval architecture is sound but the process around it failed, and the "fix before extend" sequencing.
