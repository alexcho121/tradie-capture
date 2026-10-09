"""Request bodies and shared constants (this is the A<->B contract; see docs/openapi.json)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator

# The six things the AI may fill in. Everything else on the record is evidence, not AI output.
FIELD_NAMES = ("requester", "change", "location", "extra_time_hours", "materials", "amount_mentioned")
REQUIRED_FIELDS = ("requester", "change", "location")

# captured     = evidence saved, not structured yet
# needs_review = AI structured it; the human has not looked yet (AI output is never final)
# reviewed     = the worker went through the fields
# confirmed    = the requester's confirmation was attached by the worker (not verified by us)
STATUSES = ("captured", "needs_review", "reviewed", "confirmed")

FIELD_LABELS = {
    "requester": "Who asked",
    "change": "What changed",
    "location": "Where",
    "extra_time_hours": "Estimated extra hours",
    "materials": "Materials mentioned",
    "amount_mentioned": "Amount mentioned (verbatim, never calculated)",
}


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    site_contacts: List[str] = Field(default_factory=list, max_length=50)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("site_contacts")
    @classmethod
    def _contacts(cls, v: List[str]) -> List[str]:
        out: List[str] = []
        for c in v:
            c = " ".join(str(c).split())[:80]
            if c and c not in out:
                out.append(c)
        return out


class ExtractIn(BaseModel):
    # Optional override: skip speech-to-text and structure this text instead
    # (used by the dev harness, tests and the eval script).
    transcript: Optional[str] = Field(default=None, max_length=8000)


class FieldsPatch(BaseModel):
    edits: Dict[str, Any]

    @field_validator("edits")
    @classmethod
    def _check(cls, edits: Dict[str, Any]) -> Dict[str, Any]:
        if not edits:
            raise ValueError("edits must not be empty")
        out: Dict[str, Any] = {}
        for name, value in edits.items():
            if name not in FIELD_NAMES:
                raise ValueError(f"unknown field: {name}")
            if name == "extra_time_hours":
                if value is None or value == "":
                    out[name] = None
                    continue
                if isinstance(value, bool):
                    raise ValueError("extra_time_hours must be a number of hours")
                try:
                    num = float(value)
                except (TypeError, ValueError):
                    raise ValueError("extra_time_hours must be a number of hours")
                if num != num or not (0 <= num <= 1000):
                    raise ValueError("extra_time_hours must be between 0 and 1000")
                out[name] = int(num) if num.is_integer() else round(num, 2)
            else:
                if value is None:
                    out[name] = None
                    continue
                if not isinstance(value, str):
                    raise ValueError(f"{name} must be text")
                text = " ".join(value.split())
                if len(text) > 500:
                    raise ValueError(f"{name} is too long (max 500 characters)")
                out[name] = text or None
        return out


class DeadlineIn(BaseModel):
    # The contract's notice deadline, typed in by the user. There is no default on purpose:
    # notice periods differ per contract (and differ from statutory SOP timeframes).
    notice_deadline: Optional[str] = None  # "YYYY-MM-DD" (or full ISO datetime); null clears it
    note: Optional[str] = Field(default=None, max_length=300)

    @field_validator("notice_deadline")
    @classmethod
    def _date(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        v = v.strip()
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("notice_deadline must be an ISO date like 2026-10-20")
        return v


class RecordNoteIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def _note(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        text = " ".join(v.split())
        return text or None


class ConfirmationRequestIn(BaseModel):
    to_name: Optional[str] = Field(default=None, max_length=80)
    to_phone: Optional[str] = Field(default=None, max_length=30)

    @field_validator("to_phone")
    @classmethod
    def _phone(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        cleaned = "".join(ch for ch in v if ch.isdigit() or ch == "+")
        if len(cleaned) < 6:
            raise ValueError("to_phone does not look like a phone number")
        return cleaned


# ---------------------------------------------------------------- response models (what B's UI reads)

class ProjectOut(BaseModel):
    id: str
    name: str
    site_contacts: List[str]
    created_at: str


class FieldOut(BaseModel):
    value: Optional[Union[str, float]] = None  # text, or a number of hours for extra_time_hours
    confidence: float = 0.0                    # 0..1; user-edited fields are 1.0
    source: Optional[str] = None               # "llm" | "rules" | "user" | null
    edited: bool = False                       # true once the worker changed it (show an "Edited" badge)


class EditOut(BaseModel):
    at: str
    field: str
    old: Optional[Union[str, float]] = None
    new: Optional[Union[str, float]] = None
    hash: str                                  # event hash in the capture's hash chain


class ConfirmationOut(BaseModel):
    requested_at: Optional[str] = None
    requested_to: Optional[str] = None
    confirmed_at: Optional[str] = None
    confirmed_by: Optional[str] = None
    note: Optional[str] = None
    proof_url: Optional[str] = None            # worker-attached screenshot; NOT verified by the system
    stale: bool = False                        # fields changed after the request/confirmation


class LocationOut(BaseModel):
    latitude: float
    longitude: float
    accuracy_m: Optional[float] = None
    located_at: Optional[str] = None           # device clock when the fix was taken
    source: str = "device"                     # reported by the phone; the server cannot verify it
    display_name: Optional[str] = None          # best-effort approximate address; raw coordinates remain authoritative
    provider: Optional[str] = None
    attribution: Optional[str] = None


class CaptureOut(BaseModel):
    id: str
    client_capture_id: Optional[str] = None    # the id the app generated offline (idempotency key)
    project_id: str
    created_at: str                            # server receipt time (UTC) - the authoritative timestamp
    device_captured_at: Optional[str] = None   # what the phone's clock said at capture (not trusted)
    location: Optional[LocationOut] = None     # one fix taken at capture time, device-reported
    audio_url: Optional[str] = None
    photo_url: Optional[str] = None
    transcript: Optional[str] = None
    fields: Dict[str, FieldOut]
    status: str                                # one of STATUSES
    edits: List[EditOut] = Field(default_factory=list)
    confirmation: ConfirmationOut
    note: Optional[str] = None                  # optional worker note, separate from extracted fields
    notice_deadline: Optional[str] = None      # typed in by the worker, never defaulted
    deadline_note: Optional[str] = None
    flags: List[str] = Field(default_factory=list)   # e.g. "missing:requester", "low_confidence:change"
    extraction: Dict[str, Any] = Field(default_factory=dict)  # provider, model, fallback, error, removed
    integrity: Dict[str, Any] = Field(default_factory=dict)   # event count + head hash
    client_meta: Dict[str, Any] = Field(default_factory=dict)  # device-reported timings (not trusted)


class ConfirmationRequestOut(BaseModel):
    message: str
    sms_url: str
    ref: str
    capture: CaptureOut
