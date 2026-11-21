from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class SiteConfig(models.Model):
    """Single-row settings editable from the admin."""

    site_title = models.CharField(max_length=80, default="Team Portal")
    course_name = models.CharField(
        max_length=120, blank=True, help_text="Shown under the title on the home page (optional)."
    )
    description = models.CharField(
        max_length=200, blank=True,
        help_text="Short text shown in link previews (WhatsApp, Teams, search results). Leave empty for an automatic one.",
    )
    registration_open = models.BooleanField(default=True)
    registration_deadline = models.DateTimeField(
        null=True, blank=True, help_text="Optional. After this, new teams can no longer register."
    )
    allow_team_edits = models.BooleanField(
        default=True, help_text="Untick to freeze all team details (e.g. once grading starts)."
    )
    min_team_size = models.PositiveSmallIntegerField(default=1)
    support_email = models.EmailField(blank=True, help_text="Shown to students when something goes wrong.")
    announcement = models.CharField(
        max_length=300, blank=True, help_text="Optional banner shown on every page."
    )

    @property
    def meta_description(self):
        if self.description:
            return self.description
        what = f" for {self.course_name}" if self.course_name else ""
        return f"Register your team and submit your project reports{what}."

    class Meta:
        verbose_name = "portal settings"
        verbose_name_plural = "portal settings"

    def __str__(self):
        return "Portal settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def registration_is_open(self):
        if not self.registration_open:
            return False
        return not self.registration_deadline or timezone.now() < self.registration_deadline


class Batch(models.Model):
    name = models.CharField(max_length=60, unique=True)
    max_team_size = models.PositiveSmallIntegerField(
        default=5, help_text="Largest team allowed for students of this batch."
    )
    is_active = models.BooleanField(default=True, help_text="Inactive batches disappear from the dropdown.")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "batches"

    def __str__(self):
        return self.name


class Milestone(models.Model):
    name = models.CharField(max_length=80, help_text="e.g. Milestone 2 Report")
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True, help_text="Instructions shown on the submission page.")
    template_url = models.URLField(
        blank=True, help_text="Link to the report template. Leave empty until you have it."
    )
    deadline = models.DateTimeField()
    is_active = models.BooleanField(
        default=True, help_text="The active milestone is the one students submit to. Only one can be active."
    )
    allow_late = models.BooleanField(
        default=False, help_text="Accept uploads after the deadline (they are flagged as late)."
    )
    allow_resubmission = models.BooleanField(
        default=True, help_text="Let a team replace its report (old versions are kept)."
    )
    max_file_mb = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            Milestone.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).order_by("-id").first()


class Team(models.Model):
    team_id = models.CharField(max_length=24, unique=True, editable=False)
    name = models.CharField(max_length=60)
    pin_hash = models.CharField(max_length=200, editable=False, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(Lower("name"), name="team_name_ci_unique")]

    def __str__(self):
        return f"{self.team_id} · {self.name}"

    def set_pin(self, raw):
        self.pin_hash = make_password(raw)

    def check_pin(self, raw):
        return bool(self.pin_hash) and check_password(raw, self.pin_hash)

    @property
    def leader(self):
        for m in self.members.all():  # uses prefetch cache when available
            if m.is_leader:
                return m
        return None

    @property
    def leader_name(self):
        leader = self.leader
        return leader.name if leader else "—"


class Member(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    name = models.CharField(max_length=100)
    enrolment = models.CharField(max_length=30, unique=True)
    phone = models.CharField(max_length=15)
    email = models.EmailField()
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="members")
    is_leader = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["team"], condition=models.Q(is_leader=True), name="one_leader_per_team"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.enrolment})"


class Submission(models.Model):
    """One row per (milestone, team): this is what enforces 'one report per team'."""

    milestone = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name="submissions")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="submissions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["milestone", "team"], name="one_submission_per_team")]

    def __str__(self):
        return f"{self.team.team_id} → {self.milestone}"

    @property
    def latest(self):
        return self.uploads.order_by("-version").first()

    @property
    def first(self):
        return self.uploads.order_by("version").first()


def upload_path(instance, filename):
    sub = instance.submission
    return f"{sub.milestone.slug}/{sub.team.team_id}/{filename}"


class Upload(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="uploads")
    version = models.PositiveSmallIntegerField(default=1)
    file = models.FileField(upload_to=upload_path, max_length=255)
    original_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    uploaded_by_name = models.CharField(max_length=100)
    uploaded_by_enrolment = models.CharField(max_length=30)
    uploaded_at = models.DateTimeField(default=timezone.now)
    is_late = models.BooleanField(default=False)

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["submission", "version"], name="unique_upload_version")]

    def __str__(self):
        return f"{self.submission} v{self.version}"


class Extension(models.Model):
    """Deadline override for one team (all its members) or one student (by enrolment)."""

    milestone = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name="extensions")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True, related_name="extensions")
    enrolment = models.CharField(
        max_length=30, blank=True, help_text="Use instead of a team to extend for a single student."
    )
    new_deadline = models.DateTimeField()
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        who = self.team.team_id if self.team_id else self.enrolment
        return f"{who} until {timezone.localtime(self.new_deadline):%d %b %Y %H:%M}"

    def clean(self):
        if bool(self.team_id) == bool(self.enrolment.strip()):
            raise ValidationError("Choose either a team or an enrolment number (not both, not neither).")

    def save(self, *args, **kwargs):
        self.enrolment = self.enrolment.strip().upper()
        super().save(*args, **kwargs)


class AuditLog(models.Model):
    at = models.DateTimeField(default=timezone.now, db_index=True)
    action = models.CharField(max_length=40)
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit")
    team_label = models.CharField(max_length=100, blank=True)
    detail = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-at"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.team_label}"
