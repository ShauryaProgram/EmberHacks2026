from __future__ import annotations

import asyncio
import datetime as dt
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from .config import Settings
from .utils import iso


COURSE_CODE = re.compile(r"(?<![A-Z0-9])([A-Z]{3}\d{3}[A-Z]\d)(?![A-Z0-9])", re.IGNORECASE)
SECTION_CODE = re.compile(r"\b(LEC|TUT|PRA)\s*[-_]?\s*(\d{4})\b", re.IGNORECASE)


class TimetableError(RuntimeError):
    pass


class TimetableClient:
    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.timetable_base_url,
            headers={"Accept": "application/json", "User-Agent": "EmberHacks-SchoolSync/1.0"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )

    async def __aenter__(self) -> "TimetableClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.client.aclose()

    async def course_meetings(self, course: Dict[str, Any]) -> List[Dict[str, Any]]:
        code = self._course_code(course)
        enrolled = self._section_codes(course)
        if not code or not enrolled:
            return []
        response = None
        for attempt in range(3):
            try:
                response = await self.client.get("/getCoursesByCodeAndSectionCode/" + quote(code))
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 2:
                    raise TimetableError("U of T Timetable Builder is temporarily unreachable") from exc
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            break
        if response is None or response.status_code == 404:
            return []
        try:
            response.raise_for_status()
            courses = response.json()["payload"]["pageableCourse"]["courses"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise TimetableError("U of T Timetable Builder returned an invalid response") from exc
        session = "%s9" % self.settings.academic_term_year
        offerings = [row for row in courses if session in (row.get("sessions") or [])]
        events: List[Dict[str, Any]] = []
        for offering in offerings:
            for section in offering.get("sections") or []:
                section_name = str(section.get("name") or "").upper()
                if section_name not in enrolled or section.get("cancelInd") == "Y":
                    continue
                events.extend(self._section_events(course, code, section, session))
        return events

    def _section_events(
        self, course: Dict[str, Any], code: str, section: Dict[str, Any], session: str
    ) -> List[Dict[str, Any]]:
        start_date = dt.date.fromisoformat(self.settings.academic_term_start)
        end_date = dt.date.fromisoformat(self.settings.academic_term_end)
        excluded = self._excluded_dates(self.settings.academic_term_breaks)
        timezone = ZoneInfo("America/Toronto")
        section_name = str(section["name"]).upper()
        kind = {"LEC": "lecture", "TUT": "tutorial", "PRA": "practical"}.get(
            str(section.get("teachMethod") or "").upper(), "class"
        )
        events: List[Dict[str, Any]] = []
        for index, meeting in enumerate(section.get("meetingTimes") or []):
            if meeting.get("sessionCode") != session:
                continue
            start = meeting.get("start") or {}
            end = meeting.get("end") or {}
            weekday = int(start.get("day") or 0)
            start_ms = int(start.get("millisofday") or 0)
            end_ms = int(end.get("millisofday") or 0)
            if not 1 <= weekday <= 7 or end_ms <= start_ms:
                continue
            date = start_date + dt.timedelta(days=(weekday - start_date.isoweekday()) % 7)
            while date <= end_date:
                if date not in excluded:
                    start_at = dt.datetime.combine(date, dt.time(), timezone) + dt.timedelta(milliseconds=start_ms)
                    end_at = dt.datetime.combine(date, dt.time(), timezone) + dt.timedelta(milliseconds=end_ms)
                    building = meeting.get("building") or {}
                    room = " ".join(part for part in (
                        str(building.get("buildingCode") or "").strip(),
                        str(building.get("buildingRoomNumber") or "").strip(),
                        str(building.get("buildingRoomSuffix") or "").strip(),
                    ) if part)
                    key = "timetable:%s:%s:%s:%s" % (course["id"], section_name, index, date.isoformat())
                    events.append({
                        "source_key": key,
                        "course_id": str(course["id"]),
                        "source_type": "course_meeting",
                        "title": "%s: %s" % (code, section_name),
                        "description": "%s from the official U of T Timetable Builder." % (
                            section.get("type") or "Course meeting"
                        ),
                        "start_at": iso(start_at),
                        "end_at": iso(end_at),
                        "all_day": False,
                        "status": "active",
                        "kind": kind,
                        "url": "https://ttb.utoronto.ca/",
                        "location": room or None,
                        "metadata": {
                            "provider": "uoft_timetable",
                            "course_code": code,
                            "section": section_name,
                            "teaching_method": section.get("teachMethod"),
                            "session_code": session,
                            "repetition": meeting.get("repetition"),
                            "building_url": building.get("buildingUrl"),
                            "delivery_modes": section.get("deliveryModes") or [],
                            "instructors": section.get("instructors") or [],
                            "reminders_disabled": True,
                        },
                    })
                date += dt.timedelta(days=7)
        return events

    @staticmethod
    def _course_code(course: Dict[str, Any]) -> Optional[str]:
        for value in (course.get("course_code"), course.get("name")):
            match = COURSE_CODE.search(str(value or ""))
            if match:
                return match.group(1).upper()
        return None

    @staticmethod
    def _section_codes(course: Dict[str, Any]) -> Set[str]:
        result = set()
        for section in course.get("sections") or []:
            match = SECTION_CODE.search(str(section.get("name") or ""))
            if match:
                result.add((match.group(1) + match.group(2)).upper())
        return result

    @staticmethod
    def _excluded_dates(value: str) -> Set[dt.date]:
        dates: Set[dt.date] = set()
        for part in (item.strip() for item in value.split(",") if item.strip()):
            if ":" not in part:
                dates.add(dt.date.fromisoformat(part))
                continue
            first, last = (dt.date.fromisoformat(item) for item in part.split(":", 1))
            date = first
            while date <= last:
                dates.add(date)
                date += dt.timedelta(days=1)
        return dates
