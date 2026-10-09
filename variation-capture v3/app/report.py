"""Printable record (HTML). Everything user- or model-supplied is escaped."""
from __future__ import annotations

from datetime import datetime
from html import escape as esc
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .schemas import FIELD_LABELS, FIELD_NAMES

SYDNEY = ZoneInfo("Australia/Sydney")


def to_sydney(iso: Optional[str]) -> str:
    """'2026-10-09T05:02:11.123Z' -> 'Fri 9 Oct 2026, 4:02 pm AEDT'. Falls back to the raw text."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(SYDNEY)
    except ValueError:
        return iso
    hour = dt.hour % 12 or 12
    return f"{dt:%a} {dt.day} {dt:%b %Y}, {hour}:{dt:%M} {dt:%p}".replace("AM", "am").replace("PM", "pm") + f" {dt:%Z}"


def _value(name: str, f: Dict[str, Any]) -> str:
    v = f.get("value")
    if v is None:
        return "<em>not stated</em>"
    if name == "extra_time_hours":
        return esc(f"{v:g} hours (worker/AI estimate)" if isinstance(v, (int, float)) else str(v))
    return esc(str(v))


def render_record_html(cap: Dict[str, Any], project: Dict[str, Any], events: List[Dict[str, Any]],
                       integrity: Dict[str, Any]) -> str:
    rows = []
    for name in FIELD_NAMES:
        f = cap["fields"][name]
        who = "Worker" if f.get("source") == "user" else ("AI" if f.get("value") is not None else "")
        edited = " · edited" if f.get("edited") else ""
        conf = f"{round(float(f.get('confidence') or 0) * 100)}%" if f.get("value") is not None else ""
        rows.append(
            f"<tr><th>{esc(FIELD_LABELS[name])}</th><td>{_value(name, f)}</td>"
            f"<td class='muted'>{esc(who)}{esc(edited)}</td><td class='muted'>{esc(conf)}</td></tr>"
        )

    edits = "".join(
        f"<tr><td>{esc(to_sydney(e['at']))}</td><td>{esc(FIELD_LABELS.get(e['field'], str(e['field'])))}</td>"
        f"<td>{esc(str(e['old']))} &rarr; {esc(str(e['new']))}</td><td class='mono'>{esc(e['hash'][:10])}</td></tr>"
        for e in cap["edits"]
    ) or "<tr><td colspan='4' class='muted'>No edits after capture.</td></tr>"

    log = "".join(
        f"<tr><td>{e['seq']}</td><td>{esc(to_sydney(e['at']))}</td><td>{esc(e['type'])}</td>"
        f"<td class='mono'>{esc(e['hash'][:16])}</td></tr>"
        for e in events
    )

    c = cap["confirmation"]
    if c["confirmed_at"]:
        conf_html = (
            f"Confirmation attached {esc(to_sydney(c['confirmed_at']))}"
            f"{' by ' + esc(c['confirmed_by']) if c['confirmed_by'] else ''}. "
            "<strong>Attached by the worker; not verified by this system.</strong>"
            + (f"<br>Note: {esc(c['note'])}" if c["note"] else "")
            + (f"<br><a href='{esc(c['proof_url'])}'>Proof file</a>" if c["proof_url"] else "")
        )
    elif c["requested_at"]:
        conf_html = f"Confirmation requested {esc(to_sydney(c['requested_at']))}; no reply attached yet."
    else:
        conf_html = "No confirmation requested."
    if c["stale"]:
        conf_html += "<br><strong class='warn'>Fields were changed after this confirmation.</strong>"

    deadline = (
        f"{esc(cap['notice_deadline'])}" + (f" &mdash; {esc(cap['deadline_note'])}" if cap["deadline_note"] else "")
        + "<br><span class='muted'>Entered by the worker from their contract. Not calculated by this tool.</span>"
        if cap["notice_deadline"] else "<span class='muted'>None entered.</span>"
    )
    problems = integrity.get("problems") or []
    integ = (
        "<span class='ok'>History and files are consistent with what was logged.</span>"
        if integrity.get("ok") else
        "<span class='warn'>Problems found:</span><ul>" + "".join(f"<li>{esc(p)}</li>" for p in problems) + "</ul>"
    )
    photo = (f"<img class='photo' src='{esc(cap['photo_url'])}' alt='Site photo'>"
             if cap["photo_url"] else "<p class='muted'>No photo.</p>")
    audio = (f"<p class='noprint'><audio controls src='{esc(cap['audio_url'])}'></audio></p>"
             if cap["audio_url"] else "")
    device_time = cap.get("device_captured_at")
    loc = cap.get("location")
    loc_html = ""
    if loc:
        acc = f" (±{float(loc['accuracy_m']):g} m)" if loc.get("accuracy_m") is not None else ""
        lat, lon = float(loc["latitude"]), float(loc["longitude"])
        osm = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=18/{lat}/{lon}"
        loc_html = (f"<br>Location reported by the phone: {lat}, {lon}{esc(acc)} &middot; "
                    f"<a href='{esc(osm)}'>map</a> <span class='muted'>(not verified)</span>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Variation record {esc(cap['id'])}</title>
<style>
 body{{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#111}}
 h1{{font-size:22px;margin:0}} h2{{font-size:15px;margin:22px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#444}}
 table{{border-collapse:collapse;width:100%}} th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #ddd;vertical-align:top}}
 th{{width:34%;font-weight:600}} .muted{{color:#666}} .mono{{font-family:ui-monospace,monospace;font-size:12px}}
 .photo{{max-width:100%;border:1px solid #ccc;border-radius:6px}} .warn{{color:#b00020}} .ok{{color:#0a6b2d}}
 .banner{{background:#fff7e0;border:1px solid #e6c200;padding:8px 12px;border-radius:6px;margin:12px 0}}
 blockquote{{margin:0;padding:8px 12px;background:#f5f5f5;border-left:3px solid #999}}
 @media print{{.noprint{{display:none}}}}
</style></head><body>
<h1>Variation record</h1>
<div class="muted">{esc(project.get('name', ''))} &middot; record {esc(cap['id'])} &middot; status: {esc(cap['status'])}</div>
<div class="banner"><strong>Draft evidence record, not legal advice.</strong> It shows what the worker captured and when.
It does not prove the work was agreed or is payable. AI-structured fields were reviewed by the worker only where marked.</div>
<h2>Captured</h2>
<p>{esc(to_sydney(cap['created_at']))} (received by the server){f"<br><span class='muted'>Phone clock said: {esc(str(device_time))} (not verified)</span>" if device_time else ""}{loc_html}</p>
{photo}{audio}
<h2>What was said (speech-to-text, may contain errors)</h2>
<blockquote>{esc(cap['transcript']) if cap['transcript'] else '<em>No transcript.</em>'}</blockquote>
<h2>Structured fields</h2>
<table>{''.join(rows)}</table>
<h2>Edit history</h2>
<table>{edits}</table>
<h2>Requester confirmation</h2><p>{conf_html}</p>
<h2>Contract notice deadline</h2><p>{deadline}</p>
<h2>Integrity</h2><p>{integ}</p>
<p class="muted">Head hash: <span class="mono">{esc(str(integrity.get('head_hash')))}</span><br>{esc(integrity.get('note', ''))}</p>
<table>{log}</table>
</body></html>"""
