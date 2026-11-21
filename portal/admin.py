from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from django.conf import settings

from . import emails, ids, security
from .exports import csv_response, zip_response
from .models import (
    AuditLog,
    Batch,
    Extension,
    Member,
    Milestone,
    SiteConfig,
    Submission,
    Team,
    Upload,
)
from .services import effective_deadline

admin.site.index_template = "admin/portal_index.html"


def _local(dt):
    return timezone.localtime(dt).strftime("%d %b %Y %H:%M") if dt else ""


# --- Settings / batches -------------------------------------------------------------

@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not SiteConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("name", "max_team_size", "is_active", "student_count")
    list_editable = ("max_team_size", "is_active")

    @admin.display(description="Students")
    def student_count(self, obj):
        return obj.members.count()


# --- Milestones ------------------------------------------------------------------------

@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ("name", "deadline_local", "is_active", "template_set", "allow_late", "allow_resubmission", "submitted")
    prepopulated_fields = {"slug": ("name",)}
    actions = [
        "extend_all_1_day", "extend_all_2_days", "extend_all_1_week",
        "download_reports", "export_status", "remind_pending",
    ]

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not settings.EMAIL_ENABLED:
            actions.pop("remind_pending", None)
        return actions

    @admin.display(description="Deadline")
    def deadline_local(self, obj):
        return _local(obj.deadline)

    @admin.display(boolean=True, description="Template link")
    def template_set(self, obj):
        return bool(obj.template_url)

    @admin.display(description="Teams submitted")
    def submitted(self, obj):
        return f"{obj.submissions.count()} / {Team.objects.count()}"

    def _extend(self, request, queryset, delta, label):
        for m in queryset:
            m.deadline = max(m.deadline, timezone.now()) + delta  # if already passed, count from now
            m.save()
        self.message_user(request, f"Deadline extended by {label} for everyone ({queryset.count()} milestone(s)).")

    @admin.action(description="Extend deadline for EVERYONE by 1 day")
    def extend_all_1_day(self, request, queryset):
        self._extend(request, queryset, timedelta(days=1), "1 day")

    @admin.action(description="Extend deadline for EVERYONE by 2 days")
    def extend_all_2_days(self, request, queryset):
        self._extend(request, queryset, timedelta(days=2), "2 days")

    @admin.action(description="Extend deadline for EVERYONE by 1 week")
    def extend_all_1_week(self, request, queryset):
        self._extend(request, queryset, timedelta(weeks=1), "1 week")

    @admin.action(description="Download all reports (ZIP of latest versions)")
    def download_reports(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one milestone.", messages.WARNING)
            return None
        milestone = queryset.get()
        uploads = [
            s.latest for s in milestone.submissions.select_related("team").prefetch_related("team__members")
            if s.latest
        ]
        if not uploads:
            self.message_user(request, "Nothing has been submitted yet.", messages.WARNING)
            return None
        return zip_response(f"{milestone.slug}_reports.zip", uploads)

    @admin.action(description="Export submission status (CSV, all teams)")
    def export_status(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one milestone.", messages.WARNING)
            return None
        m = queryset.get()
        subs = {s.team_id: s for s in m.submissions.all()}
        rows = []
        for team in Team.objects.prefetch_related("members__batch").order_by("team_id"):
            sub = subs.get(team.pk)
            first, latest = (sub.first, sub.latest) if sub else (None, None)
            rows.append([
                team.team_id, team.name, team.leader_name, team.members.count(),
                ", ".join(sorted({mem.batch.name for mem in team.members.all()})),
                "yes" if sub else "no",
                first.uploaded_by_name if first else "",
                _local(first.uploaded_at) if first else "",
                _local(latest.uploaded_at) if latest else "",
                latest.version if latest else "",
                "yes" if latest and latest.is_late else "",
                _local(effective_deadline(m, team)),
            ])
        return csv_response(
            f"{m.slug}_status.csv",
            ["team_id", "team", "leader", "members", "batches", "submitted", "first_uploaded_by",
             "first_uploaded_at", "last_uploaded_at", "versions", "late", "team_deadline"],
            rows,
        )

    @admin.action(description="Email a reminder to teams that haven't submitted")
    def remind_pending(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one milestone.", messages.WARNING)
            return
        m = queryset.get()
        done = set(m.submissions.values_list("team_id", flat=True))
        sent = 0
        for team in Team.objects.exclude(pk__in=done).prefetch_related("members"):
            sent += bool(emails.send_reminder(team, m, effective_deadline(m, team), request))
        self.message_user(request, f"Reminder queued for {sent} team(s).")


# --- Teams ------------------------------------------------------------------------------

class MemberInline(admin.TabularInline):
    model = Member
    extra = 0
    fields = ("name", "enrolment", "phone", "email", "batch", "is_leader", "position")


class ExtensionInline(admin.TabularInline):
    model = Extension
    extra = 0
    fields = ("milestone", "new_deadline", "reason")


class SubmittedFilter(admin.SimpleListFilter):
    title = "report (active milestone)"
    parameter_name = "report"

    def lookups(self, request, model_admin):
        return [("yes", "Submitted"), ("no", "Not submitted"), ("late", "Submitted late")]

    def queryset(self, request, queryset):
        milestone = Milestone.get_active()
        if not milestone or not self.value():
            return queryset
        done = Submission.objects.filter(milestone=milestone)
        if self.value() == "yes":
            return queryset.filter(pk__in=done.values("team_id"))
        if self.value() == "no":
            return queryset.exclude(pk__in=done.values("team_id"))
        late = Upload.objects.filter(submission__milestone=milestone, is_late=True).values("submission__team_id")
        return queryset.filter(pk__in=late)


class ExtensionActionForm(forms.Form):
    new_deadline = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
        label="New deadline",
    )
    reason = forms.CharField(max_length=200, required=False)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("team_id", "name", "leader_name", "size", "batches", "report", "created_at")
    list_filter = (SubmittedFilter, "members__batch", "created_at")
    search_fields = ("team_id", "name", "members__name", "members__enrolment", "members__email", "members__phone")
    readonly_fields = ("team_id", "created_at", "updated_at")
    inlines = [MemberInline, ExtensionInline]
    actions = ["grant_extension", "reset_pin", "remind", "export_members"]
    list_per_page = 50

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not settings.EMAIL_ENABLED:
            actions.pop("remind", None)
        return actions

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("members__batch", "submissions")

    @admin.display(description="Leader")
    def leader_name(self, obj):
        return obj.leader_name

    @admin.display(description="Size")
    def size(self, obj):
        return len(obj.members.all())

    @admin.display(description="Batches")
    def batches(self, obj):
        return ", ".join(sorted({m.batch.name for m in obj.members.all()}))

    @admin.display(description="Report")
    def report(self, obj):
        milestone = Milestone.get_active()
        sub = next((s for s in obj.submissions.all() if milestone and s.milestone_id == milestone.pk), None)
        if not sub:
            return "—"
        up = sub.latest
        label = f"v{up.version}" + (" · late" if up.is_late else "")
        return format_html('<a href="{}">{}</a>', reverse("download_upload", args=[up.pk]), label)

    @admin.action(description="Grant a deadline extension to selected teams…")
    def grant_extension(self, request, queryset):
        milestone = Milestone.get_active()
        if not milestone:
            self.message_user(request, "There is no active milestone.", messages.ERROR)
            return None
        if "apply" in request.POST:
            form = ExtensionActionForm(request.POST)
            if form.is_valid():
                for team in queryset:
                    Extension.objects.update_or_create(
                        milestone=milestone, team=team, enrolment="",
                        defaults={"new_deadline": form.cleaned_data["new_deadline"], "reason": form.cleaned_data["reason"]},
                    )
                self.message_user(request, f"Extended the deadline for {queryset.count()} team(s) until {_local(form.cleaned_data['new_deadline'])}.")
                return None
        else:
            form = ExtensionActionForm(initial={"new_deadline": timezone.localtime(milestone.deadline + timedelta(days=1))})
        return render(request, "admin/portal_extension_action.html", {
            **self.admin_site.each_context(request),
            "title": "Grant a deadline extension",
            "form": form, "teams": queryset, "milestone": milestone,
            "action_name": "grant_extension",
            "selected_ids": request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME),
            "opts": self.model._meta,
        })

    @admin.action(description="Reset team PIN (the new PIN is shown on screen)")
    def reset_pin(self, request, queryset):
        for team in queryset:
            pin = ids.generate_pin()
            team.set_pin(pin)
            team.save(update_fields=["pin_hash", "updated_at"])
            security.clear(f"pin:{team.pk}")  # also lifts any "too many wrong PINs" lockout
            emails.send_pin_reset(team, pin, request)  # no-op while EMAIL_ENABLED is off
            AuditLog.objects.create(action="pin_reset_by_admin", team=team, team_label=team.team_id,
                                    detail=f"by {request.user.get_username()}")
            self.message_user(request, f"{team.name} ({team.team_id}): new PIN is {pin}", messages.SUCCESS)

    @admin.action(description="Email a submission reminder")
    def remind(self, request, queryset):
        milestone = Milestone.get_active()
        if not milestone:
            self.message_user(request, "There is no active milestone.", messages.ERROR)
            return
        done = set(milestone.submissions.values_list("team_id", flat=True))
        sent = sum(
            bool(emails.send_reminder(t, milestone, effective_deadline(milestone, t), request))
            for t in queryset if t.pk not in done
        )
        self.message_user(request, f"Reminder queued for {sent} team(s) that haven't submitted.")

    @admin.action(description="Export members (CSV)")
    def export_members(self, request, queryset):
        rows = []
        for team in queryset.prefetch_related("members__batch"):
            for m in team.members.all():
                rows.append([team.team_id, team.name, m.name, m.enrolment, m.phone, m.email, m.batch.name,
                             "leader" if m.is_leader else ""])
        return csv_response("teams.csv", ["team_id", "team", "name", "enrolment", "phone", "email", "batch", "role"], rows)


# --- Submissions, extensions, audit -------------------------------------------------------

class UploadInline(admin.TabularInline):
    model = Upload
    extra = 0
    can_delete = True
    fields = ("version", "download", "original_name", "size", "uploaded_by_name", "uploaded_by_enrolment", "uploaded_at", "is_late")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="File")
    def download(self, obj):
        return format_html('<a href="{}">Download</a>', reverse("download_upload", args=[obj.pk])) if obj.pk else ""


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("team", "milestone", "versions", "last_upload", "late")
    list_filter = ("milestone",)
    search_fields = ("team__team_id", "team__name", "uploads__uploaded_by_name", "uploads__uploaded_by_enrolment")
    inlines = [UploadInline]
    actions = ["download_zip"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("team", "milestone").prefetch_related("uploads")

    @admin.display(description="Versions")
    def versions(self, obj):
        return len(obj.uploads.all())

    @admin.display(description="Last upload")
    def last_upload(self, obj):
        ups = list(obj.uploads.all())
        return _local(max(u.uploaded_at for u in ups)) if ups else ""

    @admin.display(boolean=True, description="Late")
    def late(self, obj):
        ups = list(obj.uploads.all())
        return bool(ups) and max(ups, key=lambda u: u.version).is_late

    @admin.action(description="Download selected reports (ZIP of latest versions)")
    def download_zip(self, request, queryset):
        uploads = [s.latest for s in queryset.select_related("team") if s.latest]
        if not uploads:
            self.message_user(request, "Nothing to download.", messages.WARNING)
            return None
        return zip_response("reports.zip", uploads)


@admin.register(Extension)
class ExtensionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "milestone", "reason", "created_at")
    search_fields = ("team__team_id", "team__name", "enrolment", "reason")
    list_filter = ("milestone",)
    autocomplete_fields = ("team",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("at", "action", "team_label", "ip", "short_detail")
    list_filter = ("action",)
    search_fields = ("team_label", "detail", "ip")
    readonly_fields = ("at", "action", "team", "team_label", "detail", "ip")

    @admin.display(description="Detail")
    def short_detail(self, obj):
        return obj.detail[:90]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
