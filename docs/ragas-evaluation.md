# RAGAS Evaluation (M13)

**Status:** built, tested offline, and run once for real on 2026-10-31 (first baseline below).

## First baseline (2026-10-31)

Judge `gpt-4.1-mini`, embeddings `text-embedding-3-small`, the app's defaults (hybrid search, reranking), the 71-question golden set. 55 questions were answered and scored; 16 were refused and are not scored. No judge failures.

| Score | Mean | Lowest | Below the review line |
|---|---|---|---|
| Faithfulness | 0.91 | 0.00 | 8 questions |
| Answer relevancy | 0.68 | 0.00 | 33 questions |
| Context precision | 0.98 | 0.33 | 2 questions |

By kind of question, faithfulness is 1.00 on the original answerable questions and falls to about 0.7 on paraphrased, held-out and near-miss ones. Retrieval looks strong (precision 0.98). The relevancy score is probably misleading: RAGAS generates questions from the answer and compares them with the real one, so bulleted procedure answers and answers that say "not specified" score low (a "not specified" answer scores 0).

The per-question scores are in `eval/results/ragas.json` (git-ignored).

## Reading the 8 low-faithfulness answers and the refusal H07 (2026-11-01)

Each answer was checked by hand against the text of the sample PDFs. Most of the low scores are the judge, not the product:

| Case | Score | Verdict |
|---|---|---|
| J06 | 0.0 | Judge error: the answer repeats a document line word for word |
| H04, J02 | 0.33, 0.5 | Judge too strict: every claim is in the documents |
| P01 | 0.5 | Right answer; the judge dislikes a fair inference ("a door sensor is an interlock") |
| K03, P02, J07 | 0.67, 0.33, 0.2 | Real but mild: the model adds a purpose or qualifier the text does not state, or joins two documents |
| N03 | 0.71 | Real: "How do I apply for maternity leave?" is answered with the generic leave process written as if it were about maternity leave; the HR policy never mentions it |
| H07 | refused | Real false refusal: the document says "do not improvise clamps or fixtures", but the line sits in a long chunk whose overall meaning scores below the 0.25 floor |

So the 0.91 faithfulness understates the product; the real defect is one pattern: **the model adds small details, or applies a general rule to a specific case the documents do not mention.**

## Changes tried on 2026-11-01 and what they measured

Every run below is the 71-question golden set through the app's own settings (hybrid search, reranking, `gpt-4.1-mini`), about 127 to 130 model calls and 80,000 to 95,000 input tokens per run (a few cents). It is the answer and refusal behaviour, not a RAGAS re-score: **faithfulness has not been re-measured after these changes.** Answers vary slightly between identical runs (N01 flipped between answering and declining), so single differences are weak evidence.

| Run | Answered | Answerable cases answered with the expected source | Unanswerable cases wrongly answered |
|---|---|---|---|
| Before | 55 / 71 | 53 / 54 | 0 / 8 |
| A: two new prompt rules | 56 / 71 | 53 / 54 | 0 / 8 |
| A + keyword rescue (B) | 59 / 71 | 54 / 54 | 1 / 8 |

**A, the prompt (kept).** Rule 7: state only what an excerpt says, no added purposes or qualifiers. Rule 8: if a distinguishing word of the question never appears in the excerpts, say so first. It made one clear difference: "What PPE do I need for welding?" is now answered "The excerpts do not mention welding specifically", followed by the general PPE guidance, instead of a bare refusal. It did **not** fix N03: the model still writes "maternity leave" into the generic process. Prompt wording alone does not stop that.

**B, the keyword rescue (built, off by default).** `RETRIEVAL_KEYWORD_RESCUE=N` lets the top N keyword matches skip the 0.25 similarity floor if reranking is on and the judge scores them 3 ("directly answers"). It fixes H07 ("Do not improvise clamps or fixtures; secure the workpiece using the approved workholding method only", cited to the right document). But it also made "What is the goods receipt procedure of the Stores department?" (unanswerable) answer with an unrelated Production SOP line, in all three runs (N = 1, 2 and 3), because the reranker itself scored that line 3. Without the rescue that question was refused in all four runs. A confident wrong answer is worse than a missed one for safety documents, so the default is 0. Enable it only if you accept that trade.

**Still open:** N03's pattern (a general rule presented as specific); re-scoring faithfulness with RAGAS after change A; whole-document questions and OCR (not started, your decision).

## Original notes (written before the first run)

## What it measures

RAGAS uses a judge model to score each answer the chatbot gives. Three scores, each 0 to 1:

| Score | Question it answers | Needs a reference answer? |
|---|---|---|
| `faithfulness` | Is every statement in the answer supported by the excerpts the model was given? | No |
| `answer_relevancy` | Does the answer address the question that was asked? | No |
| `context_precision` | Are the useful excerpts ranked first among those sent to the model? | No |

`context_recall` is **not** included: it needs a written reference answer per question, and the golden set
only records the expected document and page. Adding reference answers is the next step if you want it.

Only questions the chatbot **answered** are scored. Refusals have no answer to judge; they are counted
separately and are already covered by `eval.run_answers` (refusal behaviour on unanswerable questions).

## How it works

1. `eval/ragas_samples.py` runs the golden set through the real pipeline with the app's current defaults
   (hybrid search, reranking). A wrapper around the chat model reads the excerpts out of the prompt that was
   actually sent, so the judge sees exactly what the model saw. Citation markers like `[1]` are removed from
   the answer before judging.
2. `eval/ragas_scoring.py` (the only file that imports `ragas`) scores the samples.
3. `eval/ragas_report.py` summarises: mean and minimum per score, split by question kind, and a list of
   questions to review by hand. A judge failure becomes "no score" and is listed for review, never counted
   as a pass.

The review lines (faithfulness 0.80, relevancy 0.70, precision 0.60) are triggers for a human look, not
pass marks. Choose real targets after the first measurement.

## Setup: a separate environment

`pip install ragas` into the app's `.venv` would downgrade `openai` (3.x to 1.109) and add about 40 packages
(langchain, langgraph, datasets). So RAGAS lives in its own venv:

```powershell
py -3.12 -m venv .venv-eval
.venv-eval\Scripts\python.exe -m pip install -e ".[eval]"
```

The `eval` extra pins langchain to the 0.3 line: ragas 0.4.3 fails to import against langchain-community 0.4
(`No module named 'langchain_community.chat_models.vertexai'`). pip may print dependency warnings about
`langgraph`; ragas does not use it and they can be ignored.

## Run

Put `OPENAI_API_KEY` in `.env`, then:

```powershell
.venv-eval\Scripts\python.exe -m eval.ragas_run --limit 5                       # cheap trial
.venv-eval\Scripts\python.exe -m eval.ragas_run --out eval/results/ragas.json   # full golden set
```

Cost: one answer call (plus a rerank call) per question, then roughly 5 to 10 judge calls per answered
question. Expect cents, not dollars, for the full set; measure with `--limit` first.

## What is verified and what is not

- Verified (unit tests, offline): reading excerpts back out of the prompt, citation stripping, skipping
  refusals, score summary and NaN handling, the CLI end to end with a fake judge, and a clear error when
  ragas or the key is missing.
- Verified: ragas 0.4.3 and langchain-openai import in `.venv-eval`.
- Verified for real (2026-10-31): the call into `ragas.evaluate` worked on the first run, a 1-question trial
  and then the full golden set, with no code changes needed. The warning "LLM returned 1 generations instead
  of requested 3" comes from the answer-relevancy metric and is harmless.
- The first baseline is at the top of this page. It was run once; scores vary a little between runs, so
  compare future runs against it with care.
