"""Judge samples with the RAGAS library. This is the only module that imports ragas.

ragas and its langchain dependencies are not installed in the app's environment (they would
downgrade `openai`). Install them in a separate one, see docs/ragas-evaluation.md:

    py -3.12 -m venv .venv-eval
    .venv-eval\\Scripts\\python.exe -m pip install -e ".[eval]"
"""

from eval.ragas_report import ScoredSample, clean_score
from eval.ragas_samples import RagasSample

# Column names ragas uses for each of our metric names.
_RAGAS_COLUMNS = {
    "faithfulness": "faithfulness",
    "answer_relevancy": "answer_relevancy",
    "context_precision": "llm_context_precision_without_reference",
}


class RagasNotInstalled(Exception):
    """ragas is missing from this environment."""


def check_ragas() -> None:
    """Fail early (before any paid chatbot calls) when ragas is not installed."""
    try:
        import langchain_openai  # noqa: F401
        import ragas  # noqa: F401
    except ImportError as exc:
        raise RagasNotInstalled(str(exc)) from exc


def score_samples(
    samples: list[RagasSample],
    *,
    api_key: str,
    judge_model: str,
    embedding_model: str,
    max_workers: int = 4,
) -> list[ScoredSample]:
    """Score every sample on faithfulness, answer relevancy and context precision.

    The judge is an OpenAI chat model; answer relevancy also embeds text with `embedding_model`.
    A question the judge fails on gets `None` for that metric instead of stopping the run.
    """
    try:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        from ragas import EvaluationDataset, SingleTurnSample, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            Faithfulness,
            LLMContextPrecisionWithoutReference,
            ResponseRelevancy,
        )
        from ragas.run_config import RunConfig
    except ImportError as exc:
        raise RagasNotInstalled(str(exc)) from exc

    if not samples:
        return []
    dataset = EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=s.question,
                response=s.answer,
                retrieved_contexts=list(s.contexts),
            )
            for s in samples
        ]
    )
    result = evaluate(
        dataset,
        metrics=[Faithfulness(), ResponseRelevancy(), LLMContextPrecisionWithoutReference()],
        llm=LangchainLLMWrapper(ChatOpenAI(model=judge_model, api_key=api_key, temperature=0)),
        embeddings=LangchainEmbeddingsWrapper(
            OpenAIEmbeddings(model=embedding_model, api_key=api_key)
        ),
        run_config=RunConfig(max_workers=max_workers, timeout=120),
        raise_exceptions=False,
        show_progress=False,
    )
    rows = result.to_pandas().to_dict("records")
    return [
        ScoredSample(
            sample=sample,
            scores={name: clean_score(row.get(column)) for name, column in _RAGAS_COLUMNS.items()},
        )
        for sample, row in zip(samples, rows, strict=True)
    ]
