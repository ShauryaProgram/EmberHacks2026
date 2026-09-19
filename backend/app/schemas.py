from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class OnboardingRequest(BaseModel):
    quercus_api_token: str = Field(min_length=10, max_length=4096)
    timezone: str = "America/Toronto"
    reminder_offsets_days: List[int] = Field(default_factory=lambda: [7, 3, 1])

    @field_validator("reminder_offsets_days")
    @classmethod
    def validate_offsets(cls, values: List[int]) -> List[int]:
        valid = [value for value in values if 0 <= value <= 60]
        if not valid:
            raise ValueError("At least one reminder offset from 0 to 60 days is required")
        return sorted(set(valid), reverse=True)[:8]

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Use a valid IANA timezone, such as America/Toronto") from exc
        return value


class OnboardingResponse(BaseModel):
    onboarded: bool
    user: Dict[str, Any]
    initial_sync_queued: bool


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    start_at: str
    end_at: Optional[str] = None
    all_day: bool = False
    kind: str = "general"
    location: Optional[str] = None
    url: Optional[str] = None
    course_id: Optional[str] = None


class EventUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=20_000)
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    all_day: Optional[bool] = None
    kind: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    status: Optional[str] = None


class ReminderCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=10_000)
    notify_at: str
    due_at: Optional[str] = None
    event_id: Optional[str] = None


class ReminderUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    body: Optional[str] = Field(default=None, max_length=10_000)
    notify_at: Optional[str] = None
    due_at: Optional[str] = None
    status: Optional[str] = None
    snoozed_until: Optional[str] = None


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class PushSubscriptionRequest(BaseModel):
    endpoint: HttpUrl
    keys: PushKeys
    user_agent: Optional[str] = None


class SyncResponse(BaseModel):
    accepted: bool
    message: str


class StudyRecomputeRequest(BaseModel):
    from_date: Optional[str] = None
    horizon_days: Optional[int] = Field(default=None, ge=1, le=180)


class StudyTimeWindow(BaseModel):
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def validate_range(self) -> "StudyTimeWindow":
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class StudyPlanningWindows(BaseModel):
    weekday: StudyTimeWindow
    weekend: StudyTimeWindow


class StudyMeal(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    schedule: Literal["daily", "weekdays", "weekends"] = "daily"
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def validate_range(self) -> "StudyMeal":
        if self.start >= self.end:
            raise ValueError("meal start must be before end")
        return self


class StudyCommitment(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    category: Literal["Club", "Work", "Commute", "Exercise", "Care", "Personal"]
    days: List[int] = Field(min_length=1, max_length=7)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @field_validator("days")
    @classmethod
    def validate_days(cls, values: List[int]) -> List[int]:
        if any(value < 0 or value > 6 for value in values):
            raise ValueError("commitment days must be between 0 (Monday) and 6 (Sunday)")
        return sorted(set(values))

    @model_validator(mode="after")
    def validate_range(self) -> "StudyCommitment":
        if self.start >= self.end:
            raise ValueError("commitment start must be before end")
        return self


class StudyPreferences(BaseModel):
    focusBlock: Literal[30, 45, 60, 90] = 60
    buffer: Literal[0, 10, 15, 30] = 15
    dailyLimit: Literal[120, 180, 240, 300] = 180
    noStudyDay: int | Literal[""] = ""

    @field_validator("noStudyDay")
    @classmethod
    def validate_no_study_day(cls, value: int | str) -> int | str:
        if value != "" and (not isinstance(value, int) or value < 0 or value > 6):
            raise ValueError("noStudyDay must be empty or between 0 (Monday) and 6 (Sunday)")
        return value


class StudyPlanningProfile(BaseModel):
    version: Literal[5] = 5
    completed: bool = True
    windows: StudyPlanningWindows
    meals: List[StudyMeal] = Field(default_factory=list, max_length=12)
    commitments: List[StudyCommitment] = Field(default_factory=list, max_length=50)
    preferences: StudyPreferences
    updatedAt: Optional[str] = None

    @model_validator(mode="after")
    def validate_ids(self) -> "StudyPlanningProfile":
        meal_ids = [meal.id for meal in self.meals]
        commitment_ids = [item.id for item in self.commitments]
        if len(meal_ids) != len(set(meal_ids)):
            raise ValueError("meal ids must be unique")
        if len(commitment_ids) != len(set(commitment_ids)):
            raise ValueError("commitment ids must be unique")
        return self


class StudySettingsUpdate(BaseModel):
    weekday_start: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekday_end: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekend_start: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekend_end: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    max_daily_minutes: Optional[int] = Field(default=None, ge=30, le=720)
    weekend_max_daily_minutes: Optional[int] = Field(default=None, ge=30, le=720)
    max_session_minutes: Optional[int] = Field(default=None, ge=25, le=180)
    min_session_minutes: Optional[int] = Field(default=None, ge=15, le=90)
    break_minutes: Optional[int] = Field(default=None, ge=0, le=60)
    planning_horizon_days: Optional[int] = Field(default=None, ge=7, le=180)
    planning_profile: Optional[StudyPlanningProfile] = None


class StudySessionUpdate(BaseModel):
    status: Literal["active", "completed", "skipped"]


class NaturalLanguageInput(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
