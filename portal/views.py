import json
import logging
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import emails, ids, security
from .forms import (
    EditLoginForm,
    HARD_MAX_MEMBERS,
    LookupForm,
    RecoverForm,
    TeamBundle,
    UploadForm,
)
from .models import Batch, Member, Milestone, SiteConfig, Team, Upload
from .services import SubmissionError, audit, save_report, window_state

log = logging.getLogger(__name__)

EDIT_SESSION_SECONDS = 30 * 60
SUBMIT_SESSION_SECONDS = 60 * 60
PIN_FAIL_LIMIT = 10  # wrong PINs per team per hour before the team is locked out of editing
IP_LIMIT = 400       # lookups per IP per 10 minutes (generous: a campus may share one public IP)


def _too_many(request, bucket, limit=IP_LIMIT, window=600):
    return security.hit(f"{bucket}:{security.client_ip(request)}", window) > limit


def _batch_context():
    batches = list(Batch.objects.filter(is_active=True))
    limits = {b.pk: b.max_team_size for b in batches}
    max_size = min(max(limits.values(), default=1), HARD_MAX_MEMBERS)
    return {"batches_exist": bool(batches), "batch_limits": limits, "max_size": max_size,
            "size_options": list(range(1, max_size + 1))}


# --- Home -----------------------------------------------------------------------

def home(request):
    return render(request, "portal/home.html")


# --- Registration ---------------------------------------------------------------

def _clean_post(post):
    return {k: v for k, v in post.items() if k != "csrfmiddlewaretoken"}


def _resume_url():
    return reverse("register") + "?resume=1"


def _register_gate(request):
    config = SiteConfig.get()
    if not config.registration_is_open:
        return render(request, "portal/message.html", {
            "title": "Registration is closed",
            "text": "New teams can no longer be registered. If you think this is a mistake, contact your instructor.",
        })
    if not Batch.objects.filter(is_active=True).exists():
        return render(request, "portal/message.html", {
            "title": "Not set up yet",
            "text": "No batches have been configured yet. Please check back soon.",
        })
    return None


def register(request):
    gate = _register_gate(request)
    if gate:
        return gate
    if request.method == "POST":
        bundle = TeamBundle(request.POST)
        if bundle.is_valid():
            request.session["pending_team"] = _clean_post(request.POST)
            return redirect("register_review")
    else:
        pending = request.session.get("pending_team")
        bundle = TeamBundle(pending) if (pending and request.GET.get("resume")) else TeamBundle()
    return render(request, "portal/register.html", {"bundle": bundle, "mode": "register", **_batch_context()})


def register_review(request):
    gate = _register_gate(request)
    if gate:
        return gate
    pending = request.session.get("pending_team")
    if not pending:
        return redirect("register")
    bundle = TeamBundle(pending)
    if not bundle.is_valid():
        return redirect(_resume_url())
    return render(request, "portal/register_review.html", {"bundle": bundle, "members": bundle.summary()})


@require_POST
def register_confirm(request):
    gate = _register_gate(request)
    if gate:
        return gate
    pending = request.session.get("pending_team")
    if not pending:
        return redirect("register")
    bundle = TeamBundle(pending)
    if not bundle.is_valid():  # re-validate: someone may have taken a name / enrolment meanwhile
        messages.error(request, "Something changed while you were reviewing. Please check the details again.")
        return redirect(_resume_url())
    try:
        team, pin = bundle.create()
    except IntegrityError:
        messages.error(request, "Someone just registered with the same name or enrolment number. Please re-check.")
        return redirect(_resume_url())

    request.session.pop("pending_team", None)
    request.session["registered"] = {"team_id": team.team_id, "pin": pin}
    audit(request, "team_registered", team, f"{team.name}; {team.members.count()} members")
    emails.send_registration(team, pin, request)
    return redirect("register_done")


def register_done(request):
    info = request.session.get("registered")
    if not info:
        return redirect("home")
    team = Team.objects.filter(team_id=info["team_id"]).prefetch_related("members__batch").first()
    if not team:
        return redirect("home")
    return render(request, "portal/register_done.html", {"team": team, "pin": info["pin"]})


# --- Editing a team ---------------------------------------------------------------

def _edit_team(request):
    auth = request.session.get("edit_auth")
    if not auth or time.time() - auth["at"] > EDIT_SESSION_SECONDS:
        request.session.pop("edit_auth", None)
        return None
    return Team.objects.filter(pk=auth["pk"]).first()


def _authenticate(request, raw_id, pin):
    """Check Team ID + PIN. Returns (status, team); status is one of
    ok, malformed, unknown, bad_pin, locked, throttled. Wrong PINs are counted per team."""
    if _too_many(request, "pin-login"):
        return "throttled", None
    team_id = ids.normalize(raw_id)
    if team_id is None:
        return "malformed", None
    team = Team.objects.filter(team_id=team_id).first()
    if not team:
        return "unknown", None
    lock_key = f"pin:{team.pk}"
    if security.count(lock_key) >= PIN_FAIL_LIMIT:
        return "locked", team
    if team.check_pin((pin or "").strip()):
        security.clear(lock_key)
        return "ok", team
    security.hit(lock_key, 3600)
    return "bad_pin", team


_AUTH_MESSAGES = {
    "throttled": "Too many attempts. Please wait a few minutes and try again.",
    "locked": "This team is temporarily locked after too many wrong PINs. Use “Lost your PIN?” to get a new one by email, or try again in an hour.",
}


def team_edit_login(request):
    form = EditLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        status, team = _authenticate(request, form.cleaned_data["team_id"], form.cleaned_data["pin"])
        if status == "ok":
            request.session["edit_auth"] = {"pk": team.pk, "at": time.time()}
            return redirect("team_edit")
        form.add_error(None, _AUTH_MESSAGES.get(status, "Team ID or PIN is incorrect."))
    return render(request, "portal/edit_login.html", {"form": form})


def team_edit(request):
    team = _edit_team(request)
    if not team:
        messages.info(request, "Please enter your Team ID and PIN to edit your team.")
        return redirect("team_edit_login")
    if not SiteConfig.get().allow_team_edits:
        return render(request, "portal/message.html", {
            "title": "Editing is switched off",
            "text": "Team details are frozen right now. Contact your instructor for changes.",
        })

    if request.method == "POST":
        bundle = TeamBundle(request.POST, team=team)
        if bundle.is_valid():
            before = [f"{m.name}/{m.enrolment}" for m in team.members.all()] + [team.name]
            try:
                bundle.update()
            except IntegrityError:
                bundle.leader_error = "Someone else just used one of these enrolment numbers or the team name."
            else:
                after = [f"{m.name}/{m.enrolment}" for m in team.members.all()] + [team.name]
                audit(request, "team_edited", team, f"before={before} after={after}")
                messages.success(request, "Team details updated.")
                return redirect("team_edit")
    else:
        bundle = TeamBundle(team=team)
    return render(request, "portal/register.html", {"bundle": bundle, "mode": "edit", "team": team, **_batch_context()})


def team_edit_logout(request):
    request.session.pop("edit_auth", None)
    return redirect("home")


# --- Find my team ---------------------------------------------------------------

def team_find(request):
    form = RecoverForm(request.POST or None)
    sent = False
    if request.method == "POST" and form.is_valid():
        if _too_many(request, "recover", limit=200):
            form.add_error(None, "Too many requests. Please wait a few minutes.")
        else:
            from .validators import normalize_enrolment
            member = Member.objects.filter(
                enrolment=normalize_enrolment(form.cleaned_data["enrolment"]),
                email__iexact=form.cleaned_data["email"].strip(),
            ).select_related("team").first()
            if member:
                team = member.team
                # A team can have its PIN re-issued once per 15 minutes. The new PIN is emailed to
                # every member (never shown on screen), so knowing someone's details can't reveal it.
                if security.hit(f"pinreset:{team.pk}", 900) == 1:
                    pin = ids.generate_pin()
                    team.set_pin(pin)
                    team.save(update_fields=["pin_hash", "updated_at"])
                    security.clear(f"pin:{team.pk}")
                    emails.send_recovery(team, pin, request)
                    audit(request, "pin_reissued", team, f"requested via {member.enrolment}")
            sent = True  # same answer whether or not a match was found
    return render(request, "portal/team_find.html", {"form": form, "sent": sent})


# --- Submission -------------------------------------------------------------------

def _submit_team(request):
    auth = request.session.get("submit_auth")
    if not auth or time.time() - auth["at"] > SUBMIT_SESSION_SECONDS:
        request.session.pop("submit_auth", None)
        return None
    return Team.objects.filter(pk=auth["pk"]).prefetch_related("members").first()


def submit_lookup(request):
    milestone = Milestone.get_active()
    form = LookupForm(request.POST or None)
    not_registered = False
    if request.method == "POST" and form.is_valid():
        status, team = _authenticate(request, form.cleaned_data["team_id"], form.cleaned_data["pin"])
        if status == "ok":
            request.session["submit_auth"] = {"pk": team.pk, "at": time.time()}
            return redirect("submit_upload")
        if status == "malformed":
            form.add_error("team_id", "That doesn't look like a valid Team ID. Check for typos (IDs look like %s-K7M-2QX)." % settings.TEAM_ID_PREFIX)
            not_registered = True
        elif status == "unknown":
            not_registered = True
        elif status == "bad_pin":
            form.add_error("pin", "That PIN doesn't match this team. Use the PIN you received when registering.")
        else:
            form.add_error(None, _AUTH_MESSAGES[status])
    return render(request, "portal/submit_lookup.html", {"form": form, "milestone": milestone, "not_registered": not_registered})


def _submission_context(team, milestone):
    submission = team.submissions.filter(milestone=milestone).first()
    state = window_state(milestone, team)
    first = submission.first if submission else None
    latest = submission.latest if submission else None
    locked = bool(submission and latest and not milestone.allow_resubmission)
    return {
        "team": team,
        "milestone": milestone,
        "state": state,
        "submission": submission,
        "first": first,
        "latest": latest,
        "locked": locked,
        "members": list(team.members.all()),
    }


def submit_upload(request):
    team = _submit_team(request)
    if not team:
        return redirect("submit_lookup")
    milestone = Milestone.get_active()
    if not milestone:
        return render(request, "portal/message.html", {
            "title": "No submission open",
            "text": "There is nothing to submit right now. Please check back later.",
        })

    form = UploadForm(request.POST or None, request.FILES or None)
    ctx = _submission_context(team, milestone)

    if request.method == "POST" and not ctx["locked"] and form.is_valid():
        fail_key = f"upl:{team.pk}"
        enrolment = form.cleaned_data["enrolment"]
        member = team.members.filter(enrolment=enrolment).first()
        if security.count(fail_key) >= 30:
            form.add_error(None, "Too many failed attempts for this team. Please try again later.")
        elif not member:
            security.hit(fail_key, 3600)
            form.add_error("enrolment", "This enrolment number isn't part of the team. Check it, or ask the team leader to update the team.")
        else:
            try:
                upload = save_report(milestone, team, member, form.cleaned_data["report"])
            except ValidationError as exc:
                form.add_error("report", exc.messages[0])
            except SubmissionError as exc:
                form.add_error(None, str(exc))
            else:
                audit(request, "report_uploaded", team, f"v{upload.version} by {member.enrolment} late={upload.is_late}")
                emails.send_receipt(upload, request)
                request.session["receipt"] = upload.pk
                return redirect("submit_done")
        ctx = _submission_context(team, milestone)

    ctx["form"] = form
    return render(request, "portal/submit_upload.html", ctx)


def submit_done(request):
    pk = request.session.get("receipt")
    upload = Upload.objects.filter(pk=pk).select_related("submission__team", "submission__milestone").first() if pk else None
    if not upload:
        return redirect("home")
    team = upload.submission.team
    return render(request, "portal/submit_done.html", {"upload": upload, "team": team, "milestone": upload.submission.milestone})


def submit_leave(request):
    request.session.pop("submit_auth", None)
    return redirect("home")


# --- Staff-only -------------------------------------------------------------------

@staff_member_required
def download_upload(request, pk):
    upload = get_object_or_404(Upload.objects.select_related("submission__team"), pk=pk)
    if not upload.file:
        raise Http404
    ext = upload.file.name.rsplit(".", 1)[-1]
    response = FileResponse(upload.file.open("rb"), as_attachment=True,
                            filename=f"{upload.submission.team.team_id}_v{upload.version}.{ext}")
    response["X-Content-Type-Options"] = "nosniff"
    return response


def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


# --- Error pages ------------------------------------------------------------------

def not_found(request, exception=None):
    return render(request, "portal/404.html", status=404)


def server_error(request):
    return render(request, "portal/500.html", status=500)
