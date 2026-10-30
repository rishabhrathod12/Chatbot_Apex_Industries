"""Measure retrieval quality on the sample documents.

    python -m eval.run --provider hashing    # offline, plumbing baseline only
    python -m eval.run --provider openai     # needs OPENAI_API_KEY, the real measurement

Builds a throwaway index in a temp folder, so it never touches the app's own data.
"""

import argparse
import sys
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.rag.ingestion.embedder import EmbeddingError, get_embedder
from app.rag.vectorstore import close_client
from eval.evaluate import RetrievalReport, evaluate_retrieval
from eval.indexing import index_sample_documents
from eval.metrics import load_golden

SAMPLE_DOCS = Path(__file__).resolve().parents[1] / "data" / "sample_docs"


def _print(report: RetrievalReport, provider: str, model: str) -> None:
    print(f"Embedder: {provider} ({model})")
    for title, metrics in (("document level", report.by_document), ("page level", report.by_page)):
        row = "  ".join(f"{k}={v:.2f}" for k, v in metrics.items() if k != "n")
        print(f"  {title:<15} n={int(metrics['n'])}  {row}")
    print("  top-1 similarity (answerable vs unanswerable questions):")
    for key, value in report.score_spread.items():
        print(f"    {key:<20} {value:.3f}")
    print(f"  missed (not in top-k): {', '.join(report.misses) or 'none'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure retrieval quality on the sample documents."
    )
    parser.add_argument("--provider", choices=["hashing", "openai"], default="hashing")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    settings = Settings(embedding_provider=args.provider)
    try:
        embedder = get_embedder(settings)
    except EmbeddingError as exc:
        print(f"Cannot use the {args.provider} embedder: {exc.reason}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        try:
            store = index_sample_documents(tmp, embedder, SAMPLE_DOCS)
            report = evaluate_retrieval(load_golden(), embedder, store, top_k=args.top_k)
        finally:
            close_client(tmp)
    _print(report, args.provider, embedder.model_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
