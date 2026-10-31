"""Compare retrieval and reranking settings end to end, with the real models.

    python -m eval.compare                 # the standard set of settings, 2 runs each
    python -m eval.compare --repeat 3

For each setting it answers every golden question, `repeat` times (the model varies a little between
runs), and averages. It costs real money (about a dollar for the default run) and needs
OPENAI_API_KEY. Builds a throwaway index and never touches the app's own data.
"""

import argparse
import sys
import tempfile
from dataclasses import dataclass

from app.core.config import Settings
from app.rag.generation import LlmError, get_chat_model
from app.rag.ingestion.embedder import EmbeddingError, get_embedder
from app.rag.retrieval import RetrievalMode
from app.rag.vectorstore import close_client
from eval.evaluate import AnswerResult, evaluate_answers
from eval.indexing import index_sample_documents
from eval.metrics import load_golden
from eval.run import SAMPLE_DOCS


@dataclass(frozen=True)
class Setting:
    name: str
    mode: RetrievalMode
    min_score: float
    rerank: bool


# The gate (min_score) was calibrated at M7 on easy questions. Harder paraphrases score lower, so
# each retrieval mode is also tried with a looser gate, leaving relevance to the reranker.
STANDARD = [
    Setting("A semantic, gate 0.40            (M7)", "semantic", 0.40, False),
    Setting("B semantic, gate 0.40 + rerank", "semantic", 0.40, True),
    Setting("C semantic, gate 0.25 + rerank", "semantic", 0.25, True),
    Setting("D hybrid,   gate 0.40", "hybrid", 0.40, False),
    Setting("E hybrid,   gate 0.40 + rerank", "hybrid", 0.40, True),
    Setting("F hybrid,   gate 0.25 + rerank", "hybrid", 0.25, True),
    Setting("G hybrid,   gate 0.15 + rerank", "hybrid", 0.15, True),
]


def _counts(results: list[AnswerResult]) -> dict[str, int]:
    answerable = [r for r in results if r.expected_codes]
    answered = [r for r in answerable if r.status == "answered"]
    return {
        "answerable": len(answerable),
        "good": sum(r.cited_expected_source for r in answered),
        "false_refusal": len(answerable) - len(answered),
        "wrong_source": sum(not r.cited_expected_source for r in answered),
        "should_refuse_but_answered": sum(
            r.case_type == "unanswerable" and r.status == "answered" for r in results
        ),
        "unanswerable": sum(r.case_type == "unanswerable" for r in results),
        "not_specified_answered": sum(
            r.case_type == "not_specified" and r.status == "answered" for r in results
        ),
        "paraphrase_false_refusal": sum(
            r.tag == "paraphrase" and r.status != "answered" for r in results
        ),
    }


def _average(runs: list[dict[str, int]]) -> dict[str, float]:
    return {key: sum(run[key] for run in runs) / len(runs) for key in runs[0]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare retrieval and reranking settings.")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--tag", help="only questions with this tag, for example heldout")
    parser.add_argument(
        "--only", help="run only settings whose name starts with these letters, e.g. AC"
    )
    args = parser.parse_args()

    settings = Settings(embedding_provider="openai")
    try:
        embedder = get_embedder(settings)
        chat_model = get_chat_model(settings)
    except (EmbeddingError, LlmError) as exc:
        print(f"Cannot run: {exc.reason}", file=sys.stderr)
        return 2

    chosen = [s for s in STANDARD if not args.only or s.name[0] in args.only.upper()]
    cases = [c for c in load_golden() if not args.tag or c.tag == args.tag]
    table: list[tuple[Setting, dict[str, float]]] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            store = index_sample_documents(tmp, embedder, SAMPLE_DOCS)
            for setting in chosen:
                runs = [
                    _counts(
                        evaluate_answers(
                            cases,
                            embedder,
                            store,
                            chat_model,
                            top_k=settings.retrieval_top_k,
                            min_score=setting.min_score,
                            retrieval_mode=setting.mode,
                            use_rerank=setting.rerank,
                            workers=args.workers,
                        )
                    )
                    for _ in range(args.repeat)
                ]
                table.append((setting, _average(runs)))
                print(f"  done: {setting.name}", file=sys.stderr)
        except LlmError as exc:
            print(f"The chat model could not be used: {exc.reason}", file=sys.stderr)
            return 3
        finally:
            close_client(tmp)

    first = table[0][1]
    print(f"\nChat model: {settings.chat_model}   answers averaged over {args.repeat} run(s)")
    answerable, refuse = first["answerable"], first["unanswerable"]
    print(f"{answerable:.0f} answerable questions, {refuse:.0f} that must be refused\n")
    columns = ("good", "refused", "wrong", "para-ref", "bad-ans", "ns-ans")
    header = f"{'setting':<38}" + "".join(f"{name:>9}" for name in columns)
    print(header)
    print("-" * len(header))
    for setting, m in table:
        values = (
            m["good"],
            m["false_refusal"],
            m["wrong_source"],
            m["paraphrase_false_refusal"],
            m["should_refuse_but_answered"],
            m["not_specified_answered"],
        )
        print(f"{setting.name:<38}" + "".join(f"{value:>9.1f}" for value in values))
    print(
        "\ngood = answered from the right document   refused = valid question wrongly refused   "
        "wrong = answered from the wrong document\npara-ref = refused paraphrases (of 8)   "
        "bad-ans = questions with no answer in the documents that got an answer anyway\n"
        "ns-ans = 'documents do not specify' questions that were answered (acceptable if grounded)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
