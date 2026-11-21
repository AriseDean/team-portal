from django.conf import settings
from django.db import DatabaseError

from .models import Milestone, SiteConfig


def site(request):
    try:
        return {"site": SiteConfig.get(), "active_milestone": Milestone.get_active(),
                "email_all": settings.EMAIL_RECIPIENTS != "leader"}
    except DatabaseError:  # e.g. before the first migration
        return {"site": None, "active_milestone": None, "email_all": True}
