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

**Not yet done:** reading the 8 low-faithfulness answers (K03, P01, P02, N03, H04, J02, J06, J07), and H07, an answerable question that was refused. The per-question scores are in `eval/results/ragas.json` (git-ignored).

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
