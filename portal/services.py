import hashlib
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from . import security
from .models import AuditLog, Extension, Submission, Upload
from .validators import validate_report_file

log = logging.getLogger(__name__)


class SubmissionError(Exception):
    """Raised with a student-friendly message when an upload can't be accepted."""


def audit(request, action, team=None, detail=""):
    AuditLog.objects.create(
        action=action,
        team=team,
        team_label=(team.team_id if team else ""),
        detail=detail,
        ip=security.client_ip(request) if request is not None else None,
    )


# --- Deadlines ----------------------------------------------------------------

def effective_deadline(milestone, team, enrolment=None):
    """Latest of: the milestone deadline, any team extension, any extension for this student."""
    cond = Q(team=team)
    if enrolment:
        cond |= Q(enrolment=enrolment.upper())
    deadlines = [milestone.deadline]
    deadlines += list(Extension.objects.filter(milestone=milestone).filter(cond).values_list("new_deadline", flat=True))
    return max(deadlines)


def window_state(milestone, team, enrolment=None, now=None):
    now = now or timezone.now()
    deadline = effective_deadline(milestone, team, enrolment)
    on_time = now <= deadline
    return {
        "deadline": deadline,
        "extended": deadline > milestone.deadline,
        "on_time": on_time,
        "can_upload": on_time or milestone.allow_late,
        "will_be_late": not on_time,
    }


# --- Uploads --------------------------------------------------------------------

def save_report(milestone, team, member, uploaded):
    """Validate and store a report; one Submission per team, one Upload per version."""
    state = window_state(milestone, team, member.enrolment)
    if not state["can_upload"]:
        raise SubmissionError("The submission deadline has passed. Contact your instructor if you need an extension.")

    ext = validate_report_file(uploaded, milestone.max_file_mb)  # raises ValidationError

    sha = hashlib.sha256()
    for chunk in uploaded.chunks():
        sha.update(chunk)
    uploaded.seek(0)

    with transaction.atomic():  # SQLite is configured with IMMEDIATE transactions: writers are serialised
        submission, _ = Submission.objects.get_or_create(milestone=milestone, team=team)
        last = submission.uploads.order_by("-version").first()
        if last and not milestone.allow_resubmission:
            raise SubmissionError("Your team has already submitted its report and replacements are switched off.")
        version = (last.version + 1) if last else 1
        safe_name = f"v{version}_{team.team_id}_{slugify(team.name)[:40] or 'team'}{ext}"
        upload = Upload(
            submission=submission,
            version=version,
            original_name=uploaded.name[:255],
            size=uploaded.size,
            sha256=sha.hexdigest(),
            uploaded_by_name=member.name,
            uploaded_by_enrolment=member.enrolment,
            uploaded_at=timezone.now(),
            is_late=state["will_be_late"],
        )
        upload.file.save(safe_name, uploaded, save=False)
        try:
            upload.save()
        except Exception:
            upload.file.delete(save=False)
            raise
    return upload
