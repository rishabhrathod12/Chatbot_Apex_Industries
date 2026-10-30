# Retrieval Baseline (M7, 2026-10-31)

Command: `python -m eval.run --provider hashing`
Golden set: `eval/golden_set.json` (26 cases: 20 answerable, 6 unanswerable). Index: the six sample PDFs, 53 chunks.

## Offline word-matching embedder (`hashing-256`)

| Level | n | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|---|
| Document | 20 | 0.80 | 0.90 | 0.95 | 0.85 |
| Page | 20 | 0.70 | 0.90 | 0.90 | 0.78 |

Missed in the top 5: **A03** ("What should an operator do if an alarm is abnormal or a guard seems faulty?").

**This is a plumbing baseline, not a quality measure.** The embedder only matches shared words. A test guards against regressions (`test_offline_baseline_has_not_regressed`).

## Relevance threshold: the important finding

Top-1 similarity per question, with this embedder:

| | min | median | max |
|---|---|---|---|
| Answerable questions | 0.179 | 0.274 | |
| Unanswerable questions | | 0.339 | **0.465** |

The ranges overlap: some unanswerable questions score *higher* than some answerable ones (they share generic words like "what is the"). So **a similarity threshold alone cannot separate answerable from unanswerable questions** here. The system therefore does not rely on it. Refusal rests on three layers:

1. Relevance gate (`RETRIEVAL_MIN_SCORE`, default 0.25): skips the model when nothing is close. It catches only the clearest misses.
2. The model is told to reply `NOT_FOUND` when the excerpts don't answer the question.
3. An answer that cites no excerpt that was really provided is discarded (`uncited_answer`).

Layers 2 and 3 have only been tested with a scripted model so far. How well they work with a real model is **not yet measured**.

## Still to measure (needs `OPENAI_API_KEY`)

1. `python -m eval.run --provider openai` for real retrieval quality, and to see whether the score ranges separate with real embeddings. Then set `RETRIEVAL_MIN_SCORE` from the data (the 0.25 default is a placeholder).
2. Behaviour of the real chat model on the 6 unanswerable questions: does it reply `NOT_FOUND`, or answer with a citation anyway? Faithfulness scoring comes with RAGAS at M13.
