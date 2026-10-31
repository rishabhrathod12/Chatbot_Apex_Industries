# Retrieval and Answer Baseline (M7, measured 2026-10-31)

> **Superseded in part by section 6 (M12).** The M7 numbers below are still correct for the 49-question set they were measured on, but the relevance gate of 0.40 chosen here turned out to be too strict for harder questions. The current defaults are hybrid search + reranking + a gate of 0.25.

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

## 6. M12: hybrid search and reranking (measured 2026-10-31)

### What was added

- **Hybrid search**: meaning-based search plus keyword (BM25) search, merged by reciprocal rank fusion. The keyword half only reads chunks the user is allowed to see, and its word-rarity statistics are computed only from those chunks.
- **Reranking**: a model scores each candidate 0 to 3 on how well it *answers* the question. Candidates scoring below 2 are dropped, the rest are reordered. If the model fails or replies with anything but a strict JSON object, the original order is used.

### The golden set grew from 26 to 71 questions

The original set was too easy to show any difference (real embeddings already scored hit@5 = 1.00). Added: 10 keyword-heavy, 8 paraphrase, 5 near-miss questions (used while tuning), and two **held-out batches of 11** written *after* the settings were chosen and never used for tuning (batch 1 was looked at once for a diagnosis, so batch 2 was written afterwards as the clean check).

### Retrieval alone (real embeddings, 38 answerable questions from the first 49)

| | Semantic | Hybrid |
|---|---|---|
| All: hit@3 / MRR | 0.95 / 0.88 | **1.00 / 0.90** |
| Keyword-heavy (10): hit@1 / MRR | 0.60 / 0.74 | **0.90 / 0.95** |
| Paraphrased (8): hit@1 / MRR | **0.75 / 0.88** | 0.38 / 0.67 |
| Original (20): hit@1 / MRR | 0.95 / 0.96 | 0.95 / 0.97 |

Hybrid is a large win for exact terms and acronyms. It **hurts paraphrases at rank 1** (the right document is still in the top 3), because generic shared words pull other chunks up. Retrieval quality alone therefore does not justify hybrid; the answers below do.

### End to end (real embeddings and gpt-4.1-mini; answers averaged over repeated runs)

First comparison, 49-question set, 2 runs (38 answerable, 4 that must be refused):

| Setting | Right answer | Wrongly refused | Must-refuse answered anyway |
|---|---|---|---|
| A semantic, gate 0.40 (the M7 default) | 30.0 | **8.0** | 1.0 |
| B semantic, gate 0.40 + rerank | 30.0 | 8.0 | 0.0 |
| C semantic, gate 0.25 + rerank | 38.0 | 0.0 | 1.0 |
| D hybrid, gate 0.40 | 30.0 | 8.0 | 1.0 |
| E hybrid, gate 0.40 + rerank | 30.0 | 8.0 | 0.0 |
| F hybrid, gate 0.25 + rerank | 38.0 | 0.0 | 0.0 |

C vs F repeated 5 times: both 38/38 right; C answered exactly one must-refuse question in every run (the Stores "goods receipt" near-miss, answering with the Quality department's incoming-inspection steps), F answered none in all 5 runs.

**Held-out batch 2** (8 valid + 3 that must not be answered, written before any result on it was seen, 3 runs): A answered 7 of 8 valid questions (1 wrongly refused); F and G answered 8 of 8; nobody answered a question they should not.

**Whole 71-question set**, 3 runs (54 answerable, 8 that must be refused):

| Setting | Right answer | Wrongly refused | Must-refuse answered anyway |
|---|---|---|---|
| A semantic, gate 0.40 (M7) | 43 / 54 | **11** (20%) | 0.3 |
| **F hybrid + rerank, gate 0.25** | **53 / 54** | 1 | **0** |
| G hybrid + rerank, gate 0.15 | 54 / 54 | 0 | 1.3 |

### What each part contributed

| Change | Effect |
|---|---|
| Loosening the gate from 0.40 to 0.25 | The big gain: valid questions wrongly refused fell from 21% to about 2%. |
| Reranking | Makes the loose gate safe. Without it, near-miss passages reach the answer step. |
| Hybrid search | Better on exact terms. In answers its measurable contribution was the last near-miss (Stores goods receipt), refused 5 of 5 times. |

### A mistake in the M7 settings (recorded honestly)

The relevance gate of 0.40 was set at M7 from the 20 easy questions, whose lowest top score was 0.458. Harder paraphrases score much lower (the lowest of the new valid questions was 0.274), so on the harder set the M7 default **wrongly refused 11 of 54 valid questions (20%)**. The gate was calibrated on questions that were too easy. A similarity score is a poor yardstick for "does this answer the question"; the reranker judges that directly.

I then repeated a milder version of the same mistake: gate 0.25 was chosen after looking at the same set. The first held-out batch showed it: the correct passage was ranked first but scored 0.208 and was discarded before the reranker saw it. Lowering the gate to 0.15 (G) fixed that but let near-miss questions through (1.3 of 8 answered wrongly). **F was chosen deliberately**, accepting about one wrongly refused question in 54, because an unverifiable answer is worse than a refusal for a company-policy assistant. This is a judgement call and easy to revisit: the gate is a setting.

### Cost

One extra model call per question. Measured on 12 questions, one at a time: median **1.6 s (A) to 2.8 s (F)**, about 1.2 s slower, and roughly twice the model calls (so about twice the cost per question).

### Defaults after M12

`RETRIEVAL_MODE=hybrid`, `RERANK_ENABLED=true`, `RETRIEVAL_MIN_SCORE=0.25`. If reranking is turned off, set the gate back to about 0.40, otherwise weak matches reach the answer step.

### Limits of this evidence

- 71 questions over 6 small fictional documents, all written by the same person who built the system. Results on real, larger, messier documents will differ.
- The held-out batches are small (8 valid questions each). "F answered 8 of 8" could easily be 7 of 8 on another day.
- One chat model (gpt-4.1-mini) acting as both judge and answerer. A separate, stronger judge might do better; a weaker one worse.
- The model varies between runs, so numbers like "0 of 8" are averages over 3 to 5 runs, not guarantees.
- Keyword search reads every permitted chunk on each question (at most `LEXICAL_MAX_CHUNKS`, default 5000). That is fine for thousands of chunks and will not scale to millions; a proper search index (M10/M14) would replace it.
- The same questions that guided the choice were also used to report it, apart from the two held-out batches.

Reproduce: `python -m eval.run --provider openai --mode both` (retrieval) and `python -m eval.compare` (answers; costs about a dollar).
