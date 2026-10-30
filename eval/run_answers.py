"""Check end-to-end answers on the golden set with the real OpenAI embedder and chat model.

    python -m eval.run_answers                  # uses RETRIEVAL_MIN_SCORE from settings
    python -m eval.run_answers --min-score 0.4 --show

Needs OPENAI_API_KEY. Makes one chat call per question that passes the relevance gate, so it
costs a small fraction of a cent. Builds a throwaway index and never touches the app's own data.
"""

import argparse
import sys
import tempfile
import textwrap

from app.core.config import Settings
from app.rag.generation import LlmError, get_chat_model
from app.rag.ingestion.embedder import EmbeddingError, get_embedder
from app.rag.vectorstore import close_client
from eval.evaluate import (
    AnswerResult,
    FollowupResult,
    evaluate_answers,
    evaluate_followups,
    summarize_answers,
)
from eval.indexing import index_sample_documents
from eval.metrics import load_followups, load_golden
from eval.run import SAMPLE_DOCS


def _show(results: list[AnswerResult]) -> None:
    for r in results:
        sources = ", ".join(r.source_codes) or "-"
        print(f"\n[{r.case_id}] {r.question}")
        print(f"    {r.status}" + (f" ({r.reason})" if r.reason else "") + f"  sources: {sources}")
        if r.status == "answered":
            print(textwrap.indent(textwrap.fill(r.answer, 90), "    > "))


def _describe_followup(f: FollowupResult) -> str:
    verdict = "ok " if f.resolved else "MISS"
    return (
        f"    [{f.case_id}] {verdict} {f.question!r} -> {f.rewritten!r} "
        f"({f.status}, sources: {', '.join(f.source_codes) or '-'})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check end-to-end answers on the golden set.")
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--show", action="store_true", help="print every answer")
    args = parser.parse_args()

    settings = Settings(embedding_provider="openai")
    try:
        embedder = get_embedder(settings)
        chat_model = get_chat_model(settings)
    except (EmbeddingError, LlmError) as exc:
        print(f"Cannot run: {exc.reason}", file=sys.stderr)
        return 2

    min_score = settings.retrieval_min_score if args.min_score is None else args.min_score
    top_k = settings.retrieval_top_k if args.top_k is None else args.top_k
    with tempfile.TemporaryDirectory() as tmp:
        try:
            store = index_sample_documents(tmp, embedder, SAMPLE_DOCS)
            results = evaluate_answers(
                load_golden(), embedder, store, chat_model, top_k=top_k, min_score=min_score
            )
            followups = evaluate_followups(
                load_followups(), embedder, store, chat_model, top_k=top_k, min_score=min_score
            )
        except LlmError as exc:
            print(f"The chat model could not be used: {exc.reason}", file=sys.stderr)
            return 3
        finally:
            close_client(tmp)

    print(f"Chat model: {settings.chat_model}   min_score={min_score}   top_k={top_k}")
    summary = summarize_answers(results)
    for key, value in summary.items():
        print(f"  {key:<34} {value}")
    resolved = sum(f.resolved for f in followups)
    print(f"  follow-ups resolved                {resolved} / {len(followups)}")
    for f in followups:
        if not f.resolved or args.show:
            print(_describe_followup(f))
    if args.show:
        _show(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
