"""Staff add PDFs to their own library; other file types are for company documents (phase 8)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.permissions import Role
from tests.conftest import SAMPLE_DOCS, add_user, login

DOCS = "/api/v1/documents"
HR = (SAMPLE_DOCS / "05_HR_Policy.pdf").read_bytes()
NOTE = b"1. Rules\n- a plain text note.\n"


@pytest.fixture
def staff(
    raw_client: TestClient, session: Session, settings: Settings
) -> dict[str, dict[str, str]]:
    settings.personal_upload_extensions = [".pdf"]  # the real default, not the relaxed test one
    add_user(session, settings, "emp@apex.test", Role.EMPLOYEE, ["production"])
    add_user(session, settings, "admin@apex.test", Role.SUPER_ADMIN)
    return {
        "emp": login(raw_client, "emp@apex.test"),
        "admin": login(raw_client, "admin@apex.test"),
    }


def _personal(client: TestClient, headers: dict[str, str], name: str, content: bytes) -> object:
    return client.post(
        DOCS, headers=headers, data={"scope": "personal"}, files=[("files", (name, content))]
    )


def test_the_default_allows_only_pdfs_in_a_personal_library() -> None:
    assert Settings(_env_file=None).personal_upload_extensions == [".pdf"]  # type: ignore[call-arg]


def test_an_employee_can_add_a_pdf_but_not_other_file_types(
    raw_client: TestClient, staff: dict[str, dict[str, str]]
) -> None:
    ok = _personal(raw_client, staff["emp"], "mine.pdf", HR)
    assert ok.status_code == 201  # type: ignore[attr-defined]

    for name in ("notes.txt", "memo.docx", "NOTES.TXT", "pdf.txt"):
        refused = _personal(raw_client, staff["emp"], name, NOTE)
        assert refused.status_code == 422, name  # type: ignore[attr-defined]
        result = refused.json()["results"][0]  # type: ignore[attr-defined]
        assert (result["status"], result["reason"]) == ("rejected", "pdf_only")


def test_a_pdf_name_does_not_make_other_content_acceptable(
    raw_client: TestClient, staff: dict[str, dict[str, str]]
) -> None:
    fake = _personal(raw_client, staff["emp"], "fake.pdf", NOTE)
    assert fake.json()["results"][0]["reason"] == "content_mismatch"  # type: ignore[attr-defined]


def test_in_a_mixed_upload_the_pdf_is_kept_and_the_rest_refused(
    raw_client: TestClient, staff: dict[str, dict[str, str]]
) -> None:
    mixed = raw_client.post(
        DOCS,
        headers=staff["emp"],
        data={"scope": "personal"},
        files=[("files", ("a.pdf", HR)), ("files", ("b.txt", NOTE))],
    )
    assert mixed.status_code == 201
    assert [r["status"] for r in mixed.json()["results"]] == ["accepted", "rejected"]


def test_company_documents_may_still_be_other_types_for_the_super_admin(
    raw_client: TestClient, staff: dict[str, dict[str, str]]
) -> None:
    company = raw_client.post(
        DOCS,
        headers=staff["admin"],
        data={"scope": "enterprise", "department": "hr"},
        files=[("files", ("policy.txt", NOTE))],
    )
    assert company.status_code == 201


@pytest.mark.parametrize("bad", [[], [".exe"], [".pdf", ".zip"]])
def test_the_setting_only_accepts_known_types(bad: list[str]) -> None:
    with pytest.raises(ValueError, match="PERSONAL_UPLOAD_EXTENSIONS"):
        Settings(_env_file=None, personal_upload_extensions=bad)  # type: ignore[call-arg]
