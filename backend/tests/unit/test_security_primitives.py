import time

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.permissions import (
    Principal,
    Role,
    build_search_filter,
    can_add_company_document,
    can_manage_document,
    can_read_document,
)
from app.core.rate_limit import LoginLimiter
from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.models.document import Document

SECRET = "unit-test-secret-" + "k" * 32


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, jwt_secret_key=SECRET, **kw)  # type: ignore[call-arg]


# ---------- passwords ----------


def test_correct_password_verifies_and_wrong_one_does_not() -> None:
    stored = hash_password("correct horse battery", 10)
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterz", stored)
    assert not verify_password("", stored)


def test_the_same_password_hashes_differently_each_time() -> None:
    assert hash_password("same password!!", 10) != hash_password("same password!!", 10)


def test_the_hash_does_not_contain_the_password() -> None:
    assert "hunter2hunter2" not in hash_password("hunter2hunter2", 10)


def test_the_cost_is_stored_in_the_hash_so_it_can_change_later() -> None:
    assert hash_password("x" * 12, 10).startswith("scrypt$10$")
    assert verify_password("x" * 12, hash_password("x" * 12, 11))


@pytest.mark.parametrize(
    "garbage", ["", "plaintext", "scrypt$10$8$1$bad$bad", "bcrypt$a$b$c$d$e", "a$b$c$d$e$f$g"]
)
def test_a_malformed_stored_hash_is_just_a_failed_check(garbage: str) -> None:
    assert verify_password("anything", garbage) is False


def test_unicode_passwords_work() -> None:
    stored = hash_password("pässwörd-密码-🔑!!", 10)
    assert verify_password("pässwörd-密码-🔑!!", stored)


# ---------- access tokens ----------


def test_access_token_round_trip() -> None:
    settings = _settings()
    token = create_access_token("user-1", settings)
    assert token.expires_in == settings.access_token_minutes * 60
    assert decode_access_token(token.token, settings) == "user-1"


def test_token_holds_only_the_user_id_not_the_role() -> None:
    claims = jwt.decode(
        create_access_token("user-1", _settings()).token,
        SECRET,
        algorithms=["HS256"],
        issuer="apex-knowledge-assistant",
    )
    assert set(claims) == {"sub", "typ", "iss", "iat", "exp", "jti"}


def test_expired_token_is_rejected() -> None:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "u",
            "typ": "access",
            "iss": "apex-knowledge-assistant",
            "iat": now - 100,
            "exp": now - 10,
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(TokenError) as exc:
        decode_access_token(token, _settings())
    assert exc.value.reason == "token_expired"


def _claims(**override: object) -> dict[str, object]:
    now = int(time.time())
    base: dict[str, object] = {
        "sub": "u",
        "typ": "access",
        "iss": "apex-knowledge-assistant",
        "iat": now,
        "exp": now + 600,
    }
    return {**base, **override}


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("signed with another secret", jwt.encode(_claims(), "z" * 40, algorithm="HS256")),
        ("alg none", jwt.encode(_claims(), "", algorithm="none")),
        ("wrong issuer", jwt.encode(_claims(iss="someone-else"), SECRET, algorithm="HS256")),
        ("refresh-type token", jwt.encode(_claims(typ="refresh"), SECRET, algorithm="HS256")),
        (
            "no subject",
            jwt.encode(
                {k: v for k, v in _claims().items() if k != "sub"}, SECRET, algorithm="HS256"
            ),
        ),
        (
            "no expiry",
            jwt.encode(
                {k: v for k, v in _claims().items() if k != "exp"}, SECRET, algorithm="HS256"
            ),
        ),
        ("numeric subject", jwt.encode(_claims(sub=123), SECRET, algorithm="HS256")),
        ("not a token", "this.is.not.a.token"),
        ("empty", ""),
    ],
)
def test_bad_tokens_are_rejected(label: str, token: str) -> None:
    with pytest.raises(TokenError) as exc:
        decode_access_token(token, _settings())
    assert exc.value.reason in {"invalid_token", "token_expired"}, label


def test_a_tampered_token_is_rejected() -> None:
    token = create_access_token("user-1", _settings()).token
    head, body, signature = token.split(".")
    flipped = signature[:-2] + ("AA" if not signature.endswith("AA") else "BB")
    with pytest.raises(TokenError):
        decode_access_token(f"{head}.{body}.{flipped}", _settings())


def test_without_a_secret_no_token_is_made_or_accepted() -> None:
    no_secret = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(TokenError) as made:
        create_access_token("u", no_secret)
    assert made.value.reason == "auth_not_configured"
    with pytest.raises(TokenError) as read:
        decode_access_token("anything", no_secret)
    assert read.value.reason == "auth_not_configured"


def test_a_short_secret_is_refused_at_startup() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(_env_file=None, jwt_secret_key="too-short")  # type: ignore[call-arg]


# ---------- refresh tokens ----------


def test_refresh_tokens_are_random_and_only_the_hash_is_kept() -> None:
    raw1, hash1 = new_refresh_token()
    raw2, hash2 = new_refresh_token()
    assert raw1 != raw2
    assert hash1 == hash_refresh_token(raw1)
    assert raw1 not in hash1
    assert len(raw1) >= 40


# ---------- login limiter ----------


def test_limiter_locks_after_the_allowed_failures_and_recovers() -> None:
    now = [0.0]
    limiter = LoginLimiter(max_failures=3, window_seconds=60, clock=lambda: now[0])
    for _ in range(2):
        limiter.record_failure("k")
    assert limiter.retry_after("k") == 0
    limiter.record_failure("k")
    assert 0 < limiter.retry_after("k") <= 61
    now[0] = 61.0
    assert limiter.retry_after("k") == 0


def test_limiter_keys_are_independent_and_reset_clears() -> None:
    limiter = LoginLimiter(max_failures=1, window_seconds=60, clock=lambda: 0.0)
    limiter.record_failure("a")
    assert limiter.retry_after("a") > 0
    assert limiter.retry_after("b") == 0
    limiter.reset("a")
    assert limiter.retry_after("a") == 0


# ---------- permission rules ----------


def _who(role: Role, *departments: str, user_id: str = "u1") -> Principal:
    return Principal(user_id, f"{user_id}@x.test", user_id, role, frozenset(departments))


def _doc(
    scope: str = "enterprise",
    department: str = "hr",
    visibility: str = "department",
    owner: str = "o",
) -> Document:
    return Document(scope=scope, department=department, visibility=visibility, owner_id=owner)


ADMIN = _who(Role.SUPER_ADMIN)
MANAGER = _who(Role.DEPARTMENT_MANAGER, "hr")
EMPLOYEE = _who(Role.EMPLOYEE, "production")


@pytest.mark.parametrize(
    ("who", "doc", "expected"),
    [
        (ADMIN, _doc(department="quality"), True),
        (MANAGER, _doc(department="hr"), True),
        (MANAGER, _doc(department="quality"), False),
        (MANAGER, _doc(department="quality", visibility="company"), True),
        (EMPLOYEE, _doc(department="production"), True),
        (EMPLOYEE, _doc(department="hr"), False),
        (EMPLOYEE, _doc(department="hr", visibility="company"), True),
        (_who(Role.EMPLOYEE), _doc(department="hr", visibility="company"), True),  # no departments
        (_who(Role.EMPLOYEE), _doc(department="hr"), False),
    ],
)
def test_who_can_read_company_documents(who: Principal, doc: Document, expected: bool) -> None:
    assert can_read_document(who, doc) is expected


def test_personal_documents_are_private_even_from_a_super_admin() -> None:
    mine = _doc(scope="personal", department="", visibility="personal", owner="u1")
    assert can_read_document(_who(Role.EMPLOYEE, user_id="u1"), mine)
    assert not can_read_document(_who(Role.EMPLOYEE, user_id="u2"), mine)
    assert not can_read_document(_who(Role.SUPER_ADMIN, user_id="boss"), mine)
    assert not can_manage_document(_who(Role.SUPER_ADMIN, user_id="boss"), mine)
    assert can_manage_document(_who(Role.EMPLOYEE, user_id="u1"), mine)


@pytest.mark.parametrize(
    ("who", "doc", "expected"),
    [
        (ADMIN, _doc(department="quality"), True),
        (MANAGER, _doc(department="hr"), True),
        (MANAGER, _doc(department="quality"), False),
        (
            MANAGER,
            _doc(department="quality", visibility="company"),
            False,
        ),  # readable, not manageable
        (EMPLOYEE, _doc(department="production"), False),
    ],
)
def test_who_can_manage_company_documents(who: Principal, doc: Document, expected: bool) -> None:
    assert can_manage_document(who, doc) is expected


def test_who_can_add_company_documents() -> None:
    assert can_add_company_document(ADMIN, "quality")
    assert can_add_company_document(MANAGER, "hr")
    assert not can_add_company_document(MANAGER, "quality")
    assert not can_add_company_document(EMPLOYEE, "production")


def test_search_filter_for_each_kind_of_user() -> None:
    assert build_search_filter(ADMIN, "enterprise") is None  # unrestricted

    admin_narrow = build_search_filter(ADMIN, "enterprise", "hr")
    assert admin_narrow is not None and admin_narrow.narrow_department == "hr"
    assert admin_narrow.departments is None

    staff = build_search_filter(_who(Role.EMPLOYEE, "production", "stores"), "enterprise")
    assert staff is not None
    assert list(staff.departments or []) == ["production", "stores"]
    assert staff.include_company_wide is True

    nobody = build_search_filter(_who(Role.EMPLOYEE), "enterprise")
    assert nobody is not None
    assert list(nobody.departments or []) == []  # empty, not None: company-wide only

    own = build_search_filter(_who(Role.SUPER_ADMIN, user_id="boss"), "personal")
    assert own is not None and own.owner_id == "boss"
