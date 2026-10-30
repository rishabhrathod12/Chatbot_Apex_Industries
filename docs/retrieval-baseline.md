# Retrieval and Answer Baseline (M7, measured 2026-10-31)

Golden set: `eval/golden_set.json`, 26 cases. 20 answerable (16 single-document, 2 multi-document, 2 exact-ID), 4 `not_specified` (the documents say outright they don't give the value), 2 `unanswerable` (nothing in the documents). Index: the six sample PDFs, 53 chunks.

Commands: `python -m eval.run --provider openai` (retrieval) and `python -m eval.run_answers` (end-to-end answers).

## 1. Retrieval

| Embedder | Level | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|---|
| **OpenAI `text-embedding-3-small`** | document | 0.95 | 0.95 | **1.00** | **0.96** |
| | page | 0.95 | 0.95 | 1.00 | 0.96 |
| Offline word-matching (`hashing-256`) | document | 0.80 | 0.90 | 0.95 | 0.85 |
| | page | 0.70 | 0.90 | 0.90 | 0.78 |

With real embeddings every answerable question finds its source in the top 5 and nothing is missed. The offline embedder is only a plumbing baseline (a test guards it against regressions).

## 2. Relevance gate

Top-1 similarity with the real embedder:

| | min | median | max |
|---|---|---|---|
| Answerable (20) | 0.458 | 0.623 | |
| Unanswerable (D05, D06) | | 0.428 | 0.429 |

The ranges separate, but the gap (0.429 vs 0.458) is narrow and the unanswerable side has only 2 questions. `RETRIEVAL_MIN_SCORE` is set to **0.40**: below every answerable question, so it causes no false refusals on this set, and it only removes clear misses. The gate does not catch D05/D06 (0.429); the model's `NOT_FOUND` does. Raising it to about 0.44 would catch them but leaves a margin of about 0.015 each way, which is too thin to trust on this much data. Revisit with more questions at M13.

(With the offline embedder the ranges overlapped completely, which is why the gate is not the main defence.)

## 3. End-to-end answers (real embedder + `gpt-4.1-mini`, gate 0.40)

| Outcome | Result |
|---|---|
| Answerable questions answered | 20 / 20 |
| ... citing the expected source document | 20 / 20 |
| False refusals | 0 |
| Wrong source cited | 0 |
| `not_specified` questions (D01-D04) | 4 / 4 refused in the last runs. At gate 0.25, D03 was answered with a correct grounded statement ("the SOP does not define tolerances, use the engineering-approved specification [2]") |
| Truly unanswerable: D05 (CEO) | refused every run |
| Truly unanswerable: **D06 (Stores goods receipt)** | **refused in 7 of 9 runs; answered in 2 of 9** |

### Known weakness: related-but-different answers (D06)

Occasionally the model answers D06 with a sentence that is genuinely in the Production SOP ("Stores issues material against the work order and records the batch or heat number [1]"). The citation is valid and the sentence is true, but it answers a *different* question (issuing material, not receiving goods). The citation check cannot catch this, because the cited text exists.

I tried a stricter rule in the prompt ("related is not the same as relevant", with an unrelated example). It made things **worse**: D06 was answered in 5 of 5 runs, versus 2 of 9 before. I reverted it. Prompt wording alone is not a reliable fix here.

What would address it: RAGAS **answer relevancy** and **faithfulness** (M13) to detect and count these cases, and more unanswerable and near-miss questions in the golden set so the rate is measured on more than one example. Until then the residual risk is real, roughly "the model answers a neighbouring question about 1 time in 5 on near-miss questions", measured on one question.

## 4. Cost and variance notes

- The model is not fully deterministic even at temperature 0, so single runs can differ. Repeat runs when judging a change (as in section 3).
- A full `run_answers` run is about 22 chat calls plus embeddings, a small fraction of a cent.

## 5. Follow-up questions (M8, real models, 2026-10-31)

A follow-up is rewritten as a standalone question (one extra model call, using the last 3 turns), then retrieved and answered like any other question. Command: `python -m eval.run_answers`.

| Case | Follow-up | Rewritten as | Answered from |
|---|---|---|---|
| F01 | And what about shutdown? | What must I check before shutting down a CNC machine? | APM-PROD-MAN-001 (correct) |
| F02 | Who do I notify? | Who should I notify when reporting a machine fault? | APM-MNT-MAN-002 (correct) |
| F03 | Who is allowed to do that work? | Who is authorized to perform lockout/tagout procedures? | APM-HSE-MAN-003 (correct), plus APM-MNT-MAN-002 |

**3 of 3 resolved.** Only 3 cases, so this shows the mechanism works, not how often it works. Things not yet measured: follow-ups that change topic, follow-ups that depend on the previous *answer* rather than the previous question, and very short or ambiguous messages.
