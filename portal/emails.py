"""Plain-text emails. All sending is best-effort: a mail failure never breaks a student's request."""
import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import SiteConfig

log = logging.getLogger(__name__)


def _site_url(request=None):
    if getattr(settings, "SITE_URL", ""):
        return settings.SITE_URL
    return request.build_absolute_uri("/").rstrip("/") if request else ""


def _fmt(dt):
    return timezone.localtime(dt).strftime("%d %b %Y, %I:%M %p")


def _send(subject, body, to):
    to = sorted({e for e in to if e})
    if not to:
        return False
    try:
        EmailMessage(f"[{SiteConfig.get().site_title}] {subject}", body, to=to).send(fail_silently=False)
        return True
    except Exception:  # noqa: BLE001
        log.exception("Could not send email %r", subject)
        return False


def _members_block(team):
    return "\n".join(
        f"  - {m.name} ({m.enrolment}){'  [leader]' if m.is_leader else ''}" for m in team.members.all()
    )


def send_registration(team, pin, request=None):
    body = f"""Your team is registered.

Team name : {team.name}
Team ID   : {team.team_id}
Edit PIN  : {pin}

Members:
{_members_block(team)}

Keep the Team ID safe: you need it to submit your report. The PIN lets your team fix typos in its details.
{_site_url(request)}
"""
    return _send(f"Team registered: {team.team_id}", body, [m.email for m in team.members.all()])


def send_pin_reset(team, pin, request=None):
    body = f"""The edit PIN for team {team.name} ({team.team_id}) was reset by the instructor.

New edit PIN: {pin}
{_site_url(request)}
"""
    return _send(f"New edit PIN for {team.team_id}", body, [m.email for m in team.members.all()])


def send_team_id(member, request=None):
    team = member.team
    body = f"""You asked for your team details.

Team name : {team.name}
Team ID   : {team.team_id}

{_site_url(request)}
"""
    return _send("Your team ID", body, [member.email])


def send_receipt(upload, request=None):
    sub = upload.submission
    team = sub.team
    late = "\nNote: this upload was received after the deadline and is marked as late.\n" if upload.is_late else ""
    body = f"""Your report was received.

Milestone : {sub.milestone.name}
Team      : {team.name} ({team.team_id})
Leader    : {team.leader_name}
Uploaded by: {upload.uploaded_by_name} ({upload.uploaded_by_enrolment})
File      : {upload.original_name} ({upload.size / 1024:.0f} KB), version {upload.version}
Received  : {_fmt(upload.uploaded_at)}
Checksum  : {upload.sha256[:16]}…
{late}
{_site_url(request)}
"""
    return _send(f"Report received: {team.team_id}", body, [m.email for m in team.members.all()])


def send_reminder(team, milestone, deadline, request=None):
    body = f"""Your team ({team.name}, {team.team_id}) has not submitted the {milestone.name} yet.

Deadline: {_fmt(deadline)}

Submit here: {_site_url(request)}/submit/
"""
    return _send(f"Reminder: {milestone.name} due {_fmt(deadline)}", body, [m.email for m in team.members.all()])
