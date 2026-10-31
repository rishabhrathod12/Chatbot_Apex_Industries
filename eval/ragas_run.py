"""Score the chatbot's answers with RAGAS (faithfulness, answer relevancy, context precision).

    .venv-eval\\Scripts\\python.exe -m eval.ragas_run                # whole golden set
    .venv-eval\\Scripts\\python.exe -m eval.ragas_run --limit 5      # cheap trial run
    .venv-eval\\Scripts\\python.exe -m eval.ragas_run --out eval/results/ragas.json

Needs OPENAI_API_KEY and the separate `.venv-eval` environment (see docs/ragas-evaluation.md).
The chatbot is run with the app's current defaults (hybrid search, reranking), on a throwaway
index that never touches the app's own data. Costs a few cents for the full golden set.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.rag.generation import LlmError, get_chat_model
from app.rag.ingestion.embedder import EmbeddingError, get_embedder
from app.rag.vectorstore import close_client
from eval.indexing import index_sample_documents
from eval.metrics import load_golden
from eval.ragas_report import ScoredSample, format_report, summarize_scores
from eval.ragas_samples import SkippedCase, collect_samples
from eval.ragas_scoring import RagasNotInstalled, check_ragas, score_samples
from eval.run import SAMPLE_DOCS


def _save(path: Path, scored: list[ScoredSample], skipped: list[SkippedCase]) -> None:
    payload = {
        "summary": summarize_scores(scored),
        "scored": [
            {
                "case_id": s.sample.case_id,
                "type": s.sample.case_type,
                "tag": s.sample.tag,
                "question": s.sample.question,
                "answer": s.sample.answer,
                "scores": s.scores,
            }
            for s in scored
        ],
        "skipped": [
            {"case_id": k.case_id, "type": k.case_type, "reason": k.reason} for k in skipped
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score answers on the golden set with RAGAS.")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions")
    parser.add_argument("--workers", type=int, default=4, help="parallel judge calls")
    parser.add_argument("--out", type=Path, default=None, help="write per-question scores (JSON)")
    args = parser.parse_args(argv)

    try:
        check_ragas()
    except RagasNotInstalled as exc:
        print(
            f"ragas is not installed in this environment ({exc}).\n"
            'Use the separate one: .venv-eval\\Scripts\\python.exe -m pip install -e ".[eval]"',
            file=sys.stderr,
        )
        return 4

    settings = Settings(embedding_provider="openai")
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    try:
        embedder = get_embedder(settings)
        chat_model = get_chat_model(settings)
    except (EmbeddingError, LlmError) as exc:
        print(f"Cannot run: {exc.reason}. Put OPENAI_API_KEY in .env.", file=sys.stderr)
        return 2

    cases = load_golden()[: args.limit]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            store = index_sample_documents(tmp, embedder, SAMPLE_DOCS)
            samples, skipped = collect_samples(
                cases,
                embedder,
                store,
                chat_model,
                top_k=settings.retrieval_top_k,
                min_score=settings.retrieval_min_score,
                retrieval_mode=settings.retrieval_mode,
                use_rerank=settings.rerank_enabled,
                rerank_top_n=settings.rerank_top_n,
                rerank_min_relevance=settings.rerank_min_relevance,
                workers=args.workers,
            )
        except LlmError as exc:
            print(f"The chat model could not be used: {exc.reason}", file=sys.stderr)
            return 3
        finally:
            close_client(tmp)

    scored = score_samples(
        samples,
        api_key=key,
        judge_model=settings.chat_model,
        embedding_model=settings.embedding_model,
        max_workers=args.workers,
    )
    print(
        format_report(
            scored, skipped, judge=settings.chat_model, embeddings=settings.embedding_model
        )
    )
    if args.out:
        _save(args.out, scored, skipped)
        print(f"  per-question scores written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
