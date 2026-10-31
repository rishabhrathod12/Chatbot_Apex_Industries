"""The permission matrix again, with hybrid search and reranking switched ON.

Keyword search and reranking are new ways for text to reach a model, so they get the same
scrutiny: for every user, nothing from a forbidden document may reach the search results, the
reranking prompt or the answering prompt. Reuses the users and documents of the main matrix.
"""

from collections.abc import Iterator

import pytest

from app.core.config import get_settings
from tests.integration.test_access_control_matrix import (
    ALL,
    CAN_READ,
    CODE,
    DOCS,
    QUERY,
    USERS,
    World,
    world,  # noqa: F401  (a fixture, used by name)
)

# Words that appear in only one document, so an exact keyword search for them is the sharpest test.
UNIQUE_TERM = {
    "safety": "light curtains",
    "hr": "leave balance",
    "production": "job traveler",
    "maintenance": "alarm code",
    "quality": "concession",
}
MODES = {
    "hybrid": {"retrieval_mode": "hybrid", "rerank_enabled": False},
    "hybrid+rerank": {"retrieval_mode": "hybrid", "rerank_enabled": True},
}


@pytest.fixture(params=list(MODES))
def mode(request: pytest.FixtureRequest, world: World) -> Iterator[str]:
    original = world.client.app.dependency_overrides[get_settings]  # type: ignore[attr-defined]
    changed = world.settings.model_copy(update=MODES[request.param])
    world.client.app.dependency_overrides[get_settings] = lambda: changed  # type: ignore[attr-defined]
    yield request.param
    world.client.app.dependency_overrides[get_settings] = original  # type: ignore[attr-defined]


def _questions() -> list[str]:
    return [*QUERY.values(), *(f"What does it say about {term}?" for term in UNIQUE_TERM.values())]


@pytest.mark.parametrize("user", USERS)
def test_search_returns_only_permitted_documents(world: World, mode: str, user: str) -> None:
    allowed = {CODE[k] for k in CAN_READ[user]}
    for query in _questions():
        response = world.client.post(
            "/api/v1/search", headers=world.headers[user], json={"query": query, "top_k": 20}
        )
        assert response.status_code == 200
        found = {hit["doc_code"] for hit in response.json()}
        assert found <= allowed, f"{mode}: {user} got {found - allowed} for {query!r}"


@pytest.mark.parametrize("user", USERS)
def test_search_still_finds_exact_terms_in_permitted_documents(
    world: World,
    mode: str,
    user: str,
) -> None:
    """The keyword half must not over-block: a rare word is found in a readable document."""
    for doc in CAN_READ[user]:
        response = world.client.post(
            "/api/v1/search",
            headers=world.headers[user],
            json={"query": UNIQUE_TERM[doc], "top_k": 20},
        )
        assert CODE[doc] in {h["doc_code"] for h in response.json()}, f"{mode}: {user} / {doc}"


@pytest.mark.parametrize("user", USERS)
def test_no_forbidden_text_reaches_either_model(world: World, mode: str, user: str) -> None:
    before = len(world.chat.calls)
    for question in _questions():
        response = world.client.post(
            "/api/v1/chat", headers=world.headers[user], json={"question": question}
        )
        assert response.status_code == 200

    sent = world.chat.calls[before:]
    assert sent, "the model was never called"
    everything = "\n".join(message["content"] for call in sent for message in call)
    for doc in ALL - CAN_READ[user]:
        assert CODE[doc] not in everything, f"{mode}: {user}: {CODE[doc]} reached a model"
    # The user typed these words in their own question, so the question line may contain them. A
    # line from a retrieved passage may not.
    leaks = [
        f"{UNIQUE_TERM[doc]!r}: {line[:80]}"
        for doc in ALL - CAN_READ[user]
        for call in sent
        for message in call
        for line in message["content"].split("\n")
        if UNIQUE_TERM[doc] in line and not line.startswith("Question:")
    ]
    assert not leaks, (
        f"{mode}: {user}: passage text from a forbidden document reached a model: {leaks}"
    )


def test_with_reranking_on_the_judge_really_is_called(world: World, mode: str) -> None:
    before = len(world.chat.calls)
    world.client.post(
        "/api/v1/chat", headers=world.headers["admin"], json={"question": QUERY["safety"]}
    )
    judged = [c for c in world.chat.calls[before:] if "judge how well passages" in c[0]["content"]]
    assert bool(judged) == (mode == "hybrid+rerank")


@pytest.mark.parametrize("doc", [d for d in DOCS if d not in ("safety", "hr")])
def test_a_user_outside_the_department_gets_nothing_for_its_exact_terms(
    world: World,
    mode: str,
    doc: str,
) -> None:
    reply = world.client.post(
        "/api/v1/search",
        headers=world.headers["stores_emp"],
        json={"query": UNIQUE_TERM[doc], "top_k": 20},
    ).json()
    assert CODE[doc] not in {hit["doc_code"] for hit in reply}
