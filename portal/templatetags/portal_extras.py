from django import template
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from ..models import AuditLog, Batch, Extension, Member, Milestone, Submission, Team, Upload

register = template.Library()


@register.inclusion_tag("admin/portal_stats.html")
def portal_stats():
    milestone = Milestone.get_active()
    teams = Team.objects.count()
    ctx = {"milestone": milestone, "teams": teams, "students": Member.objects.count(), "checks": []}

    if milestone:
        submitted = Submission.objects.filter(milestone=milestone).count()
        late = Upload.objects.filter(submission__milestone=milestone, is_late=True).values("submission").distinct().count()
        ctx.update(
            submitted=submitted,
            pending=max(teams - submitted, 0),
            late=late,
            pct=round(100 * submitted / teams) if teams else 0,
            extensions=Extension.objects.filter(milestone=milestone, new_deadline__gt=timezone.now()).count(),
            past_deadline=timezone.now() > milestone.deadline,
        )
        ctx["by_batch"] = Batch.objects.annotate(
            students=Count("members", distinct=True),
            submitted_students=Count(
                "members", filter=Q(members__team__submissions__milestone=milestone), distinct=True
            ),
        ).filter(students__gt=0)
        if not milestone.template_url:
            ctx["checks"].append("The report template link is not set yet (edit the milestone).")
    else:
        ctx["checks"].append("There is no active milestone, so students cannot submit.")

    if settings.EMAIL_ENABLED and "console" in settings.EMAIL_BACKEND:
        ctx["checks"].append("Email is not configured (messages are only printed to the server log). Set EMAIL_* in .env.")
    if Batch.objects.filter(is_active=True, name__regex=r"^Batch [0-9]+$").exists():
        ctx["checks"].append("Placeholder batches (“Batch 1…”) exist. Rename them under Batches.")
    ctx["recent"] = AuditLog.objects.select_related("team")[:8]
    return ctx
