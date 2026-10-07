import pytest
from datetime import datetime, timedelta, timezone
from starlette.requests import Request

from app.database import Database
from app.models.internal import UserSession
from app.services.auth import AuthService
from app.services.filter_utils import extract_filter_params, resolve_effective_library_ids
from app.services.sync import generate_series_slug


def test_slug_generation_multilingual():
    # ASCII english
    slug1 = generate_series_slug("Berserk")
    assert slug1.startswith("berserk-")

    # German with umlauts
    slug2 = generate_series_slug("Lena, meine kleine Schwester")
    assert slug2.startswith("lena-meine-kleine-schwester-")

    # Japanese strings
    jp1 = "余命一年の君が僕に残してくれたもの"
    jp2 = "無職転生 ～異世界行ったら本気だす～"
    slug_jp1 = generate_series_slug(jp1)
    slug_jp2 = generate_series_slug(jp2)

    assert slug_jp1 != slug_jp2
    assert slug_jp1.startswith("s-")
    assert slug_jp2.startswith("s-") or len(slug_jp2) > 3


def test_filter_utils_extraction():
    # 1. Comma separated query param
    req1 = Request({"type": "http", "query_string": b"library_id=14,20&series_id=s-1,s-2&search=test&unpaged=true"})
    f1 = extract_filter_params(req1)
    assert f1["library_ids"] == [14, 20]
    assert f1["series_ids"] == ["s-1", "s-2"]
    assert f1["search"] == "test"
    assert f1["unpaged"] is True

    # 2. CamelCase and multiple query params
    req2 = Request({"type": "http", "query_string": b"libraryId=14&libraryId=20&seriesId=series-123"})
    f2 = extract_filter_params(req2)
    assert f2["library_ids"] == [14, 20]
    assert f2["series_ids"] == ["series-123"]

    # 3. Post body with Komga SearchCondition schema
    req3 = Request({"type": "http", "query_string": b""})
    body3 = {
        "condition": {
            "allOf": [
                {"libraryId": {"operator": "is", "value": "20"}},
                {"seriesId": {"operator": "is", "value": "s-xyz"}},
            ]
        },
        "fullTextSearch": "light novel",
    }
    f3 = extract_filter_params(req3, body3)
    assert f3["library_ids"] == [20]
    assert f3["series_ids"] == ["s-xyz"]
    assert f3["search"] == "light novel"

    # 4. Post body with flat libraryIds array
    body4 = {"libraryIds": ["10", "15"], "seriesIds": ["s-1"]}
    f4 = extract_filter_params(req3, body4)
    assert f4["library_ids"] == [10, 15]
    assert f4["series_ids"] == ["s-1"]

    # 5. Post body with nested id dictionary (e.g. {"condition": {"series": {"id": "s-nested"}}})
    body5 = {"condition": {"series": {"id": "s-nested"}, "library": {"id": 20}}}
    f5 = extract_filter_params(req3, body5)
    assert f5["library_ids"] == [20]
    assert f5["series_ids"] == ["s-nested"]


def test_effective_library_resolution():
    admin = UserSession(
        user_id=1,
        username="admin",
        token="tok1",
        is_admin=True,
        assigned_library_ids=[],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    restricted_user = UserSession(
        user_id=2,
        username="reader",
        token="tok2",
        is_admin=False,
        assigned_library_ids=[20],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    unrestricted_user = UserSession(
        user_id=3,
        username="open_reader",
        token="tok3",
        is_admin=False,
        assigned_library_ids=[],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    # Admin with no filter -> returns None (all libraries)
    assert resolve_effective_library_ids(admin, []) is None

    # Admin with filter [14, 20] -> returns [14, 20]
    assert resolve_effective_library_ids(admin, [14, 20]) == [14, 20]

    # Unrestricted non-admin with no filter -> returns None (all libraries)
    assert resolve_effective_library_ids(unrestricted_user, []) is None

    # Unrestricted non-admin requesting [20] -> returns [20]
    assert resolve_effective_library_ids(unrestricted_user, [20]) == [20]

    # Restricted user with no filter -> defaults strictly to [20]
    assert resolve_effective_library_ids(restricted_user, []) == [20]

    # Restricted user requesting library 20 -> returns [20]
    assert resolve_effective_library_ids(restricted_user, [20]) == [20]

    # Restricted user requesting library 14 -> returns [] (unauthorized!)
    assert resolve_effective_library_ids(restricted_user, [14]) == []

    # Restricted user requesting [14, 20] -> returns [20] (filtered intersection)
    assert resolve_effective_library_ids(restricted_user, [14, 20]) == [20]


@pytest.mark.asyncio
async def test_database_multi_library_and_unpaged_queries(tmp_path):
    db_file = tmp_path / "filter_test.db"
    db = Database(str(db_file))
    await db.connect()

    # Create 2 libraries
    await db.upsert_libraries([
        {"id": 14, "name": "Stories", "paths": []},
        {"id": 20, "name": "Light Novel", "paths": []},
    ])

    # Series in library 14
    await db.upsert_series_batch([
        {"id": "14-s1", "library_id": 14, "name": "Story 1", "slug": "s1", "books_count": 1},
        {"id": "20-s2", "library_id": 20, "name": "Novel 1", "slug": "s2", "books_count": 2},
    ])

    # Books in both series
    await db.upsert_books_batch([
        {"id": 1, "series_id": "14-s1", "library_id": 14, "name": "Story Vol 1", "number": 1.0},
        {"id": 2, "series_id": "20-s2", "library_id": 20, "name": "Novel Vol 1", "number": 1.0},
        {"id": 3, "series_id": "20-s2", "library_id": 20, "name": "Novel Vol 2", "number": 2.0},
    ])

    # Query series for library 20 only
    s_lib20, count20 = await db.get_series_list(library_ids=[20])
    assert count20 == 1
    assert s_lib20[0]["id"] == "20-s2"

    # Query series for library 14 only
    s_lib14, count14 = await db.get_series_list(library_ids=[14])
    assert count14 == 1
    assert s_lib14[0]["id"] == "14-s1"

    # Query series with empty list (unauthorized) -> returns 0
    s_none, count_none = await db.get_series_list(library_ids=[])
    assert count_none == 0
    assert len(s_none) == 0

    # Query books unpaged for series 20-s2
    books_unpaged, b_total = await db.get_books_list(series_id="20-s2", unpaged=True)
    assert b_total == 2
    assert len(books_unpaged) == 2
    assert books_unpaged[0]["series_name"] == "Novel 1"

    # Query books with library_ids filter
    b_lib20, _ = await db.get_books_list(library_ids=[20])
    assert len(b_lib20) == 2
