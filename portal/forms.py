import re

from django import forms
from django.db import IntegrityError, transaction
from django.forms import formset_factory
from django.utils.text import capfirst

from . import ids
from .models import Batch, Member, SiteConfig, Team
from .validators import (
    normalize_enrolment,
    normalize_phone,
    validate_email_domain,
    validate_enrolment,
    validate_phone,
)

HARD_MAX_MEMBERS = 12  # sanity cap; the real limit comes from each batch


class MemberForm(forms.Form):
    name = forms.CharField(max_length=100, label="Full name")
    enrolment = forms.CharField(max_length=30, label="Enrolment no.")
    phone = forms.CharField(max_length=20, label="Phone")
    email = forms.EmailField(label="Email")
    batch = forms.ModelChoiceField(queryset=Batch.objects.none(), label="Batch", empty_label="Select batch")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["batch"].queryset = Batch.objects.filter(is_active=True)
        attrs = {
            "name": {"placeholder": "Full name", "autocomplete": "off"},
            "enrolment": {"placeholder": "Enrolment number", "autocomplete": "off", "class": "upper", "spellcheck": "false"},
            "phone": {"placeholder": "10-digit mobile", "inputmode": "tel", "autocomplete": "off"},
            "email": {"placeholder": "name@example.com", "autocomplete": "off"},
        }
        for key, value in attrs.items():
            self.fields[key].widget.attrs.update(value)

    def clean_name(self):
        name = re.sub(r"\s+", " ", self.cleaned_data["name"]).strip()
        if len(name) < 2:
            raise forms.ValidationError("Enter the student's full name.")
        return name

    def clean_enrolment(self):
        value = normalize_enrolment(self.cleaned_data["enrolment"])
        validate_enrolment(value)
        return value

    def clean_phone(self):
        value = normalize_phone(self.cleaned_data["phone"])
        validate_phone(value)
        return value

    def clean_email(self):
        value = self.cleaned_data["email"].strip().lower()
        validate_email_domain(value)
        return value


class BaseMemberFormSet(forms.BaseFormSet):
    def __init__(self, *args, exclude_team=None, **kwargs):
        self.exclude_team = exclude_team
        super().__init__(*args, **kwargs)

    def _construct_form(self, i, **kwargs):
        kwargs["empty_permitted"] = False  # a blank member row is an error, never silently skipped
        return super()._construct_form(i, **kwargs)

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        config = SiteConfig.get()
        rows = [f.cleaned_data for f in self.forms]
        size = len(rows)

        if size < config.min_team_size:
            raise forms.ValidationError(f"A team needs at least {config.min_team_size} member(s).")
        limit = min(r["batch"].max_team_size for r in rows)
        if size > limit:
            raise forms.ValidationError(
                f"With these batches a team can have at most {limit} members (you entered {size})."
            )

        seen = {}
        for form in self.forms:
            enrol = form.cleaned_data["enrolment"]
            if enrol in seen:
                form.add_error("enrolment", "This enrolment number is entered twice in this team.")
            seen[enrol] = form
        taken = Member.objects.filter(enrolment__in=seen.keys())
        if self.exclude_team is not None:
            taken = taken.exclude(team=self.exclude_team)
        for enrol in taken.values_list("enrolment", flat=True):
            seen[enrol].add_error(
                "enrolment",
                "Already registered in a team. Ask your teammate for the Team ID, or use “Find my team”.",
            )
        if any(self.errors):
            raise forms.ValidationError("Please fix the highlighted fields.")


MemberFormSet = formset_factory(
    MemberForm,
    formset=BaseMemberFormSet,
    extra=0,
    min_num=1,
    validate_min=True,
    max_num=HARD_MAX_MEMBERS,
    validate_max=True,
    absolute_max=HARD_MAX_MEMBERS,
)


class TeamForm(forms.Form):
    name = forms.CharField(max_length=60, label="Team name")

    def __init__(self, *args, team=None, **kwargs):
        self.team = team
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"placeholder": "e.g. Byte Builders", "autocomplete": "off"})

    def clean_name(self):
        name = re.sub(r"\s+", " ", self.cleaned_data["name"]).strip()
        if len(name) < 2:
            raise forms.ValidationError("Choose a team name (at least 2 characters).")
        clash = Team.objects.filter(name__iexact=name)
        if self.team is not None:
            clash = clash.exclude(pk=self.team.pk)
        if clash.exists():
            raise forms.ValidationError("A team with this name already exists. Please pick another.")
        return name


class TeamBundle:
    """Team name + member rows + leader choice, validated and saved together."""

    def __init__(self, data=None, team=None):
        self.team = team
        initial = None
        leader_raw = None
        team_initial = None
        if data is None and team is not None:
            members = list(team.members.all())
            initial = [
                {"name": m.name, "enrolment": m.enrolment, "phone": m.phone, "email": m.email, "batch": m.batch_id}
                for m in members
            ]
            leader_raw = next((str(i) for i, m in enumerate(members) if m.is_leader), None)
            team_initial = {"name": team.name}
        elif data is not None:
            leader_raw = data.get("leader")

        self.team_form = TeamForm(data, team=team, prefix="team", initial=team_initial)
        self.formset = MemberFormSet(data, prefix="members", initial=initial, exclude_team=team)
        self.is_bound = data is not None
        self.leader_raw = leader_raw
        self.leader_error = None
        self._valid = None

    # -- helpers used by templates ---------------------------------------------
    @property
    def rows(self):
        """Forms to render server-side (none for a brand-new, unbound registration)."""
        if not self.is_bound and self.team is None:
            return []
        return self.formset.forms

    @property
    def leader_index(self):
        try:
            return int(self.leader_raw)
        except (TypeError, ValueError):
            return None

    def is_valid(self):
        if self._valid is not None:
            return self._valid
        team_ok = self.team_form.is_valid()
        set_ok = self.formset.is_valid()
        leader_ok = True
        if set_ok:
            idx = self.leader_index
            if idx is None or not (0 <= idx < len(self.formset.forms)):
                self.leader_error = "Select who the team leader is."
                leader_ok = False
        self._valid = team_ok and set_ok and leader_ok
        return self._valid

    @property
    def non_field_errors(self):
        errs = list(self.formset.non_form_errors())
        if self.leader_error:
            errs.append(self.leader_error)
        return errs

    # -- persistence ------------------------------------------------------------
    def summary(self):
        """List of dicts (name, enrolment, phone, email, batch, is_leader) for review pages."""
        out = []
        for i, f in enumerate(self.formset.forms):
            row = dict(f.cleaned_data)
            row["is_leader"] = i == self.leader_index
            out.append(row)
        return out

    def _write_members(self, team):
        for i, row in enumerate(self.summary()):
            Member.objects.create(
                team=team,
                position=i,
                is_leader=row["is_leader"],
                name=row["name"],
                enrolment=row["enrolment"],
                phone=row["phone"],
                email=row["email"],
                batch=row["batch"],
            )

    def create(self):
        """Create the team; returns (team, raw_pin) or raises IntegrityError on a race."""
        pin = ids.generate_pin()
        with transaction.atomic():
            team_id = ids.generate()
            while Team.objects.filter(team_id=team_id).exists():
                team_id = ids.generate()
            team = Team(team_id=team_id, name=self.team_form.cleaned_data["name"])
            team.set_pin(pin)
            team.save()
            self._write_members(team)
        return team, pin

    def update(self):
        with transaction.atomic():
            self.team.name = self.team_form.cleaned_data["name"]
            self.team.save()
            self.team.members.all().delete()
            self._write_members(self.team)
        return self.team


class LookupForm(forms.Form):
    team_id = forms.CharField(max_length=40, label="Team ID")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team_id"].widget.attrs.update(
            {"placeholder": "TM-K7M-2QX", "class": "upper mono", "autocomplete": "off", "spellcheck": "false", "autofocus": True}
        )


class EditLoginForm(forms.Form):
    team_id = forms.CharField(max_length=40, label="Team ID")
    pin = forms.CharField(max_length=12, label="Edit PIN")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team_id"].widget.attrs.update({"placeholder": "TM-K7M-2QX", "class": "upper mono", "autocomplete": "off"})
        self.fields["pin"].widget.attrs.update({"placeholder": "6-digit PIN", "inputmode": "numeric", "autocomplete": "off"})


class RecoverForm(forms.Form):
    enrolment = forms.CharField(max_length=30, label="Enrolment no.")
    email = forms.EmailField(label="Email you registered with")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["enrolment"].widget.attrs.update({"placeholder": "Enrolment number", "class": "upper", "autocomplete": "off"})
        self.fields["email"].widget.attrs.update({"placeholder": "name@example.com"})


class UploadForm(forms.Form):
    enrolment = forms.CharField(max_length=30, label="Your enrolment no.")
    report = forms.FileField(label="Report (PDF or Word)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["enrolment"].widget.attrs.update({"placeholder": "Enrolment number", "class": "upper", "autocomplete": "off"})
        self.fields["report"].widget.attrs.update({"accept": ".pdf,.doc,.docx"})

    def clean_enrolment(self):
        return normalize_enrolment(self.cleaned_data["enrolment"])
