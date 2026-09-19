import httpx
import pytest

from app.config import Settings
from app.timetable import TimetableClient


def response_payload():
    return {
        "payload": {
            "pageableCourse": {
                "courses": [{
                    "code": "CSC110Y5",
                    "sessions": ["20269"],
                    "sections": [
                        {
                            "name": "LEC0101",
                            "type": "Lecture",
                            "teachMethod": "LEC",
                            "cancelInd": "N",
                            "meetingTimes": [{
                                "start": {"day": 1, "millisofday": 9 * 60 * 60 * 1000},
                                "end": {"day": 1, "millisofday": 11 * 60 * 60 * 1000},
                                "building": {"buildingCode": "MN", "buildingRoomNumber": "1210"},
                                "sessionCode": "20269",
                                "repetition": "WEEKLY",
                            }],
                            "deliveryModes": [{"session": "20269", "mode": "INPER"}],
                            "instructors": [{"firstName": "Ada", "lastName": "Lovelace"}],
                        },
                        {
                            "name": "PRA0104",
                            "type": "Practical",
                            "teachMethod": "PRA",
                            "cancelInd": "N",
                            "meetingTimes": [],
                        },
                        {
                            "name": "LEC0102",
                            "type": "Lecture",
                            "teachMethod": "LEC",
                            "cancelInd": "N",
                            "meetingTimes": [],
                        },
                    ],
                }]
            }
        }
    }


@pytest.mark.asyncio
async def test_timetable_expands_only_enrolled_sections_and_skips_breaks(tmp_path):
    def handler(request):
        assert request.url.path.endswith("/getCoursesByCodeAndSectionCode/CSC110Y5")
        return httpx.Response(200, json=response_payload())

    settings = Settings(
        database_path=tmp_path / "x.db",
        secret_key_path=tmp_path / "key",
        vapid_key_path=tmp_path / "vapid",
        academic_term_start="2026-09-08",
        academic_term_end="2026-11-02",
        academic_term_breaks="2026-10-12,2026-10-26:2026-10-30",
    )
    course = {
        "id": 42,
        "course_code": "CSC110Y5_Fall_2026_All Sections",
        "sections": [
            {"name": "CSC110Y5-F-LEC0101-20269"},
            {"name": "CSC110Y5-F-PRA0104-20269"},
        ],
    }
    async with TimetableClient(settings, httpx.MockTransport(handler)) as client:
        events = await client.course_meetings(course)

    dates = [event["start_at"][:10] for event in events]
    assert dates == ["2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05", "2026-10-19", "2026-11-02"]
    assert all(event["kind"] == "lecture" for event in events)
    assert all(event["location"] == "MN 1210" for event in events)
    assert all(event["metadata"]["reminders_disabled"] for event in events)


def test_canvas_identifiers_are_extracted_from_course_and_sections(tmp_path):
    settings = Settings(database_path=tmp_path / "x.db", secret_key_path=tmp_path / "key",
                        vapid_key_path=tmp_path / "vapid")
    client = TimetableClient(settings)
    course = {
        "course_code": "CSC110Y5_Fall_2026_All Sections",
        "sections": [
            {"name": "CSC110Y5-F-LEC0101-20269"},
            {"name": "CSC110Y5-F-PRA0104-20269"},
        ],
    }

    assert client._course_code(course) == "CSC110Y5"
    assert client._section_codes(course) == {"LEC0101", "PRA0104"}
