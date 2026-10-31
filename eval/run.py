"""Measure retrieval quality on the sample documents.

    python -m eval.run --provider hashing              # offline, plumbing baseline only
    python -m eval.run --provider openai               # needs OPENAI_API_KEY, the real measurement
    python -m eval.run --provider openai --mode both   # semantic vs hybrid, side by side

Builds a throwaway index in a temp folder, so it never touches the app's own data.
"""

import argparse
import sys
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.rag.ingestion.embedder import EmbeddingError, get_embedder
from app.rag.retrieval import RetrievalMode
from app.rag.vectorstore import close_client
from eval.evaluate import RetrievalReport, evaluate_retrieval
from eval.indexing import index_sample_documents
from eval.metrics import load_golden

SAMPLE_DOCS = Path(__file__).resolve().parents[1] / "data" / "sample_docs"


def _row(metrics: dict[str, float]) -> str:
    return "  ".join(f"{k}={v:.2f}" for k, v in metrics.items() if k != "n")


def _print(report: RetrievalReport, provider: str, model: str, mode: str) -> None:
    print(f"Embedder: {provider} ({model})   retrieval: {mode}")
    for title, metrics in (("document level", report.by_document), ("page level", report.by_page)):
        print(f"  {title:<15} n={int(metrics['n'])}  {_row(metrics)}")
    if len(report.by_tag) > 1:
        print("  by kind of question (document level):")
        for tag, metrics in sorted(report.by_tag.items()):
            print(f"    {tag:<12} n={int(metrics['n']):<3} {_row(metrics)}")
    print("  top-1 similarity (answerable vs unanswerable questions):")
    for key, value in report.score_spread.items():
        print(f"    {key:<20} {value:.3f}")
    print(f"  missed (not in top-k): {', '.join(report.misses) or 'none'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure retrieval quality on the sample documents."
    )
    parser.add_argument("--provider", choices=["hashing", "openai"], default="hashing")
    parser.add_argument("--mode", choices=["semantic", "hybrid", "both"], default="semantic")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    settings = Settings(embedding_provider=args.provider)
    try:
        embedder = get_embedder(settings)
    except EmbeddingError as exc:
        print(f"Cannot use the {args.provider} embedder: {exc.reason}", file=sys.stderr)
        return 2

    modes: list[RetrievalMode] = ["semantic", "hybrid"] if args.mode == "both" else [args.mode]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            store = index_sample_documents(tmp, embedder, SAMPLE_DOCS)
            cases = load_golden()
            for index, mode in enumerate(modes):
                if index:
                    print()
                report = evaluate_retrieval(
                    cases,
                    embedder,
                    store,
                    top_k=args.top_k,
                    mode=mode,
                    candidates=settings.retrieval_candidates,
                )
                _print(report, args.provider, embedder.model_name, mode)
        finally:
            close_client(tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
