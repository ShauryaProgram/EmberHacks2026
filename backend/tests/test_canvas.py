import httpx
import pytest

from app.canvas import CanvasClient, CanvasError, extract_document
from app.config import Settings


@pytest.mark.asyncio
async def test_canvas_pagination_and_authorization(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"id": 2}])
        return httpx.Response(200, json=[{"id": 1}], headers={
            "Link": '<https://q.utoronto.ca/api/v1/courses?page=2>; rel="next"'
        })

    settings = Settings(database_path=tmp_path / "x.db", secret_key_path=tmp_path / "key",
                        vapid_key_path=tmp_path / "vapid")
    async with CanvasClient("secret", settings, httpx.MockTransport(handler)) as client:
        rows = await client.paginate("/api/v1/courses")
    assert [row["id"] for row in rows] == [1, 2]
    assert all(request.headers["Authorization"] == "Bearer secret" for request in requests)


@pytest.mark.asyncio
async def test_foreign_pagination_is_rejected(tmp_path):
    def handler(_request):
        return httpx.Response(200, json=[], headers={
            "Link": '<https://evil.example/api/v1/courses?page=2>; rel="next"'
        })

    settings = Settings(database_path=tmp_path / "x.db", secret_key_path=tmp_path / "key",
                        vapid_key_path=tmp_path / "vapid")
    async with CanvasClient("secret", settings, httpx.MockTransport(handler)) as client:
        with pytest.raises(CanvasError, match="unsafe pagination"):
            await client.paginate("/api/v1/courses")


def test_plain_text_document_extraction():
    assert extract_document(b"First\nSecond", "syllabus.txt") == "First\nSecond"


@pytest.mark.asyncio
async def test_calendar_query_is_limited_to_fall_2026_and_requests_series(tmp_path):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=[])

    settings = Settings(database_path=tmp_path / "x.db", secret_key_path=tmp_path / "key",
                        vapid_key_path=tmp_path / "vapid", academic_term_season="fall",
                        academic_term_year=2026)
    async with CanvasClient("secret", settings, httpx.MockTransport(handler)) as client:
        await client.calendar_events("42")

    params = captured[0].url.params
    assert params["context_codes[]"] == "course_42"
    assert params["start_date"].startswith("2026-08-01")
    assert params["end_date"].startswith("2027-01-15")
    assert params["includes[]"] == "series_natural_language"


@pytest.mark.asyncio
async def test_calendar_hydrates_section_child_event_ids(tmp_path):
    def handler(request):
        if request.url.path == "/api/v1/calendar_events/901":
            return httpx.Response(200, json={
                "id": 901,
                "context_code": "course_section_88",
                "start_at": "2026-09-21T15:00:00-04:00",
            })
        return httpx.Response(200, json=[{
            "id": 900,
            "hidden": True,
            "child_events": [901],
            "start_at": "2026-09-21T14:00:00-04:00",
        }])

    settings = Settings(database_path=tmp_path / "x.db", secret_key_path=tmp_path / "key",
                        vapid_key_path=tmp_path / "vapid")
    async with CanvasClient("secret", settings, httpx.MockTransport(handler)) as client:
        rows = await client.calendar_events("42")

    assert rows[0]["child_events"][0]["id"] == 901
    assert rows[0]["child_events"][0]["context_code"] == "course_section_88"
