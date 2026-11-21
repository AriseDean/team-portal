import io
import shutil
import tempfile
import zipfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import ids
from .models import Batch, Extension, Member, Milestone, SiteConfig, Submission, Team, Upload
from .services import effective_deadline

TMP_MEDIA = tempfile.mkdtemp(prefix="portal-test-")
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def make_pdf(size=2000):
    return b"%PDF-1.4\n" + b"0" * size + b"\n%%EOF"


def make_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def reg_post(team_name, members, leader=0):
    data = {
        "team-name": team_name, "leader": str(leader),
        "members-TOTAL_FORMS": str(len(members)), "members-INITIAL_FORMS": "0",
        "members-MIN_NUM_FORMS": "0", "members-MAX_NUM_FORMS": "12",
    }
    for i, member in enumerate(members):
        for key, value in member.items():
            data[f"members-{i}-{key}"] = value
    return data


@override_settings(CACHES=LOCMEM, MEDIA_ROOT=TMP_MEDIA, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_ASYNC=False, EMAIL_RECIPIENTS="all", EMAIL_ENABLED=True,
                   TRUST_PROXY=False, STORAGES={
                       "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                       "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class PortalCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.pins = {}
        self.batch_a = Batch.objects.create(name="Batch A", max_team_size=5)
        self.batch_b = Batch.objects.create(name="Batch B", max_team_size=3)
        self.milestone = Milestone.objects.create(
            name="Milestone 2", slug="m2", deadline=timezone.now() + timedelta(days=3),
        )

    def member(self, n, batch=None, **extra):
        return {"name": f"Student {n}", "enrolment": f"E2100{n:03d}", "phone": f"98765432{n:02d}",
                "email": f"s{n}@example.com", "batch": (batch or self.batch_a).pk, **extra}

    def register(self, name="Alpha", numbers=(1, 2, 3), leader=0, batch=None, client=None):
        client = client or self.client
        resp = client.post(reverse("register"), reg_post(name, [self.member(n, batch) for n in numbers], leader))
        self.assertRedirects(resp, reverse("register_review"), fetch_redirect_response=False, msg_prefix=str(getattr(resp, "context", None) and resp.context["bundle"].formset.errors))
        resp = client.post(reverse("register_confirm"))
        self.assertRedirects(resp, reverse("register_done"), fetch_redirect_response=False)
        team = Team.objects.get(name=name)
        pin = client.session["registered"]["pin"]
        self.pins[team.pk] = pin
        return team, pin

    def verify(self, team, client=None):
        client = client or self.client
        resp = client.post(reverse("submit_lookup"), {"team_id": team.team_id, "pin": self.pins[team.pk]})
        self.assertRedirects(resp, reverse("submit_upload"), fetch_redirect_response=False)

    def upload(self, enrolment, content=None, name="report.pdf", client=None):
        client = client or self.client
        f = SimpleUploadedFile(name, content if content is not None else make_pdf())
        return client.post(reverse("submit_upload"), {"enrolment": enrolment, "report": f})


class TeamIdTests(PortalCase):
    def test_roundtrip_and_formats(self):
        for _ in range(50):
            tid = ids.generate()
            self.assertEqual(ids.normalize(tid), tid)
            self.assertEqual(ids.normalize(tid.lower().replace("-", " ")), tid)

    def test_typos_are_caught(self):
        tid = ids.generate()
        code = tid.replace("-", "")[2:]
        alphabet = ids.ALPHABET
        # every single-character substitution must fail the check
        for pos in range(len(code)):
            for ch in alphabet:
                if ch != code[pos]:
                    mutated = code[:pos] + ch + code[pos + 1:]
                    self.assertIsNone(ids.normalize("TM" + mutated), (code, mutated))
        # adjacent transpositions of different characters must fail too
        for pos in range(len(code) - 1):
            if code[pos] != code[pos + 1]:
                swapped = code[:pos] + code[pos + 1] + code[pos] + code[pos + 2:]
                self.assertIsNone(ids.normalize("TM" + swapped))

    def test_garbage(self):
        for bad in ["", None, "TM-000-000", "hello", "TM-ABC", "TM-ABC-DEFG"]:
            self.assertIsNone(ids.normalize(bad))


class RegistrationTests(PortalCase):
    def test_full_flow(self):
        team, pin = self.register(leader=1)
        self.assertRegex(team.team_id, r"^TM-[A-Z0-9]{3}-[A-Z0-9]{3}$")
        self.assertEqual(team.members.count(), 3)
        self.assertEqual(team.leader.name, "Student 2")
        self.assertTrue(team.check_pin(pin))
        self.assertNotIn(pin, team.pin_hash)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(sorted(mail.outbox[0].to), ["s1@example.com", "s2@example.com", "s3@example.com"])
        self.assertIn(team.team_id, mail.outbox[0].body)
        page = self.client.get(reverse("register_done"))
        self.assertContains(page, team.team_id)
        self.assertContains(page, pin)

    def test_review_shows_details_before_creation(self):
        self.client.post(reverse("register"), reg_post("Alpha", [self.member(1)], 0))
        resp = self.client.get(reverse("register_review"))
        self.assertContains(resp, "Student 1")
        self.assertFalse(Team.objects.exists())

    def test_normalises_input(self):
        m = self.member(1)
        m.update(enrolment=" e2100 001 ", phone="+91 98765-43201", email="S1@Example.COM", name="  jane   doe ")
        self.client.post(reverse("register"), reg_post("Alpha", [m], 0))
        self.client.post(reverse("register_confirm"))
        stored = Member.objects.get()
        self.assertEqual((stored.enrolment, stored.phone, stored.email, stored.name),
                         ("E2100001", "9876543201", "s1@example.com", "jane doe"))

    def _errors(self, resp):
        return resp.context["bundle"].formset.errors, resp.context["bundle"].non_field_errors

    def test_rejects_duplicate_enrolment_across_teams(self):
        self.register("Alpha", (1, 2))
        resp = self.client.post(reverse("register"), reg_post("Beta", [self.member(2), self.member(9)]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Already registered", str(resp.context["bundle"].formset.errors[0]["enrolment"]))

    def test_rejects_duplicate_enrolment_in_same_form(self):
        resp = self.client.post(reverse("register"), reg_post("Beta", [self.member(1), self.member(1)]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["bundle"].formset.errors[1])

    def test_blank_rows_are_errors_not_skipped(self):
        data = reg_post("Beta", [self.member(1), {"name": "", "enrolment": "", "phone": "", "email": "", "batch": ""}])
        resp = self.client.post(reverse("register"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["bundle"].formset.errors[1])

    def test_requires_leader(self):
        data = reg_post("Beta", [self.member(1), self.member(2)])
        del data["leader"]
        resp = self.client.post(reverse("register"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("leader", " ".join(resp.context["bundle"].non_field_errors).lower())
        data["leader"] = "7"
        self.assertEqual(self.client.post(reverse("register"), data).status_code, 200)

    def test_batch_team_size_limit(self):
        # Batch B allows only 3
        resp = self.client.post(reverse("register"), reg_post("Beta", [self.member(n, self.batch_b) for n in (1, 2, 3, 4)]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("at most 3", " ".join(resp.context["bundle"].non_field_errors))
        # a mixed team is limited by its strictest batch
        mixed = [self.member(1), self.member(2), self.member(3), self.member(4, self.batch_b)]
        self.assertEqual(self.client.post(reverse("register"), reg_post("Gamma", mixed)).status_code, 200)
        ok = [self.member(n, self.batch_a) for n in (1, 2, 3, 4, 5)]
        self.assertEqual(self.client.post(reverse("register"), reg_post("Delta", ok)).status_code, 302)

    def test_team_name_unique_case_insensitive(self):
        self.register("Alpha", (1,))
        resp = self.client.post(reverse("register"), reg_post("aLPHA", [self.member(5)]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["bundle"].team_form.errors)

    def test_invalid_fields(self):
        bad = self.member(1)
        bad.update(phone="12345", email="nope", enrolment="x")
        resp = self.client.post(reverse("register"), reg_post("Beta", [bad]))
        errs = resp.context["bundle"].formset.errors[0]
        self.assertEqual(set(errs), {"phone", "email", "enrolment"})

    def test_closed_registration(self):
        cfg = SiteConfig.get()
        cfg.registration_open = False
        cfg.save()
        self.assertContains(self.client.get(reverse("register")), "Registration is closed")

    def test_resume_keeps_entered_data(self):
        self.client.post(reverse("register"), reg_post("Alpha", [self.member(1), self.member(2)], 1))
        resp = self.client.get(reverse("register") + "?resume=1")
        self.assertContains(resp, 'value="Student 2"')
        self.assertEqual(resp.context["bundle"].leader_index, 1)


class EditTests(PortalCase):
    def test_wrong_pin_and_lockout(self):
        team, pin = self.register()
        for _ in range(10):
            resp = self.client.post(reverse("team_edit_login"), {"team_id": team.team_id, "pin": "000000" if pin != "000000" else "111111"})
            self.assertContains(resp, "incorrect")
        resp = self.client.post(reverse("team_edit_login"), {"team_id": team.team_id, "pin": pin})
        self.assertContains(resp, "locked")

    def test_edit_flow(self):
        team, pin = self.register()
        self.assertEqual(self.client.get(reverse("team_edit")).status_code, 302)  # not signed in
        resp = self.client.post(reverse("team_edit_login"), {"team_id": team.team_id.lower(), "pin": pin})
        self.assertRedirects(resp, reverse("team_edit"), fetch_redirect_response=False)
        page = self.client.get(reverse("team_edit"))
        self.assertContains(page, 'value="Student 3"')

        members = [self.member(1), self.member(2), self.member(3)]
        members[2]["name"] = "Fixed Name"
        members[0], members[1] = members[1], members[0]  # swapping enrolments must not trip uniqueness
        resp = self.client.post(reverse("team_edit"), reg_post("Alpha Renamed", members, leader=2))
        self.assertRedirects(resp, reverse("team_edit"), fetch_redirect_response=False)
        team.refresh_from_db()
        self.assertEqual(team.name, "Alpha Renamed")
        self.assertEqual(team.leader.name, "Fixed Name")
        self.assertEqual(team.members.count(), 3)
        self.assertTrue(team.audit.filter(action="team_edited").exists())

    def test_edit_cannot_steal_other_teams_enrolment(self):
        team, pin = self.register("Alpha", (1, 2))
        self.register("Beta", (7,), client=self.client_class())
        self.client.post(reverse("team_edit_login"), {"team_id": team.team_id, "pin": pin})
        resp = self.client.post(reverse("team_edit"), reg_post("Alpha", [self.member(1), self.member(7)]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["bundle"].formset.errors[1])

    def test_edit_can_be_frozen(self):
        team, pin = self.register()
        self.client.post(reverse("team_edit_login"), {"team_id": team.team_id, "pin": pin})
        cfg = SiteConfig.get()
        cfg.allow_team_edits = False
        cfg.save()
        self.assertContains(self.client.get(reverse("team_edit")), "Editing is switched off")


class FindTeamTests(PortalCase):
    def test_pin_reissued_and_emailed_to_whole_team(self):
        team, old_pin = self.register("Alpha", (1, 2))
        mail.outbox.clear()
        r1 = self.client.post(reverse("team_find"), {"enrolment": "e2100001", "email": "S1@example.com"})
        self.assertContains(r1, "Check your inbox")
        self.assertNotContains(r1, old_pin)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(sorted(mail.outbox[0].to), ["s1@example.com", "s2@example.com"])
        self.assertIn(team.team_id, mail.outbox[0].body)
        new_pin = [l for l in mail.outbox[0].body.splitlines() if l.startswith("New PIN")][0].split(":")[1].strip()
        team.refresh_from_db()
        self.assertTrue(team.check_pin(new_pin))
        self.assertFalse(team.check_pin(old_pin))

    def test_no_email_or_change_when_details_do_not_match_but_same_response(self):
        team, pin = self.register("Alpha", (1,))
        mail.outbox.clear()
        r = self.client.post(reverse("team_find"), {"enrolment": "E2100001", "email": "wrong@example.com"})
        self.assertContains(r, "Check your inbox")
        self.assertEqual(len(mail.outbox), 0)
        team.refresh_from_db()
        self.assertTrue(team.check_pin(pin))

    def test_reissue_has_a_cooldown(self):
        team, _ = self.register("Alpha", (1,))
        mail.outbox.clear()
        for _ in range(3):
            self.client.post(reverse("team_find"), {"enrolment": "E2100001", "email": "s1@example.com"})
        self.assertEqual(len(mail.outbox), 1)

    def test_reissue_unlocks_a_locked_team(self):
        team, pin = self.register("Alpha", (1,))
        for _ in range(10):
            self.client.post(reverse("submit_lookup"), {"team_id": team.team_id, "pin": "000000" if pin != "000000" else "111111"})
        self.assertContains(self.client.post(reverse("submit_lookup"), {"team_id": team.team_id, "pin": pin}), "locked")
        self.client.post(reverse("team_find"), {"enrolment": "E2100001", "email": "s1@example.com"})
        team.refresh_from_db()
        new_pin = [l for l in mail.outbox[-1].body.splitlines() if l.startswith("New PIN")][0].split(":")[1].strip()
        resp = self.client.post(reverse("submit_lookup"), {"team_id": team.team_id, "pin": new_pin})
        self.assertRedirects(resp, reverse("submit_upload"), fetch_redirect_response=False)


class SubmissionTests(PortalCase):
    def setUp(self):
        super().setUp()
        self.team, self.pin = self.register("Alpha", (1, 2, 3), leader=1)
        mail.outbox.clear()

    def test_unknown_and_malformed_ids(self):
        c = self.client_class()
        r = c.post(reverse("submit_lookup"), {"team_id": "nonsense", "pin": "123456"})
        self.assertContains(r, "valid Team ID")
        self.assertContains(r, "isn't registered")
        valid_but_unused = ids.generate()
        r = c.post(reverse("submit_lookup"), {"team_id": valid_but_unused, "pin": "123456"})
        self.assertContains(r, "isn't registered")
        self.assertContains(r, reverse("register"))
        self.assertEqual(c.get(reverse("submit_upload")).status_code, 302)  # can't skip the ID step

    def test_team_id_alone_is_not_enough(self):
        c = self.client_class()
        r = c.post(reverse("submit_lookup"), {"team_id": self.team.team_id})  # no PIN at all
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get(reverse("submit_upload")).status_code, 302)
        self.assertNotContains(r, "Alpha")

    def test_wrong_pin_is_rejected_without_leaking_team_details(self):
        c = self.client_class()
        wrong = "000000" if self.pins[self.team.pk] != "000000" else "111111"
        r = c.post(reverse("submit_lookup"), {"team_id": self.team.team_id, "pin": wrong})
        self.assertContains(r, "PIN doesn&#x27;t match")
        self.assertNotContains(r, "Student 2")
        self.assertEqual(c.get(reverse("submit_upload")).status_code, 302)
        self.assertEqual(c.post(reverse("submit_upload"), {"enrolment": "E2100001"}).status_code, 302)  # still signed out

    def test_pin_attempts_are_limited_per_team_across_submit_and_edit(self):
        pin = self.pins[self.team.pk]
        wrong = "000000" if pin != "000000" else "111111"
        for i in range(10):
            view = "submit_lookup" if i % 2 else "team_edit_login"
            self.client.post(reverse(view), {"team_id": self.team.team_id, "pin": wrong})
        r = self.client.post(reverse("submit_lookup"), {"team_id": self.team.team_id, "pin": pin})
        self.assertContains(r, "locked")
        self.assertEqual(self.client.get(reverse("submit_upload")).status_code, 302)

    def test_upload_requires_team_member_enrolment(self):
        self.verify(self.team)
        resp = self.upload("E2100099")
        self.assertContains(resp, "isn&#x27;t part of the team")
        self.assertFalse(Submission.objects.exists())

    def test_successful_upload_and_receipt(self):
        self.verify(self.team)
        resp = self.upload("e2100002")
        self.assertRedirects(resp, reverse("submit_done"), fetch_redirect_response=False)
        up = Upload.objects.get()
        self.assertEqual((up.version, up.uploaded_by_name, up.is_late), (1, "Student 2", False))
        self.assertEqual(len(up.sha256), 64)
        self.assertTrue(up.file.name.startswith(f"m2/{self.team.team_id}/v1_"))
        self.assertTrue(up.file.name.endswith(".pdf"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertContains(self.client.get(reverse("submit_done")), "Report received")

    def test_teammate_sees_already_submitted_with_leader_and_original_uploader(self):
        self.verify(self.team)
        self.upload("E2100003")
        other = self.client_class()
        self.verify(self.team, other)
        page = other.get(reverse("submit_upload"))
        self.assertContains(page, "Submitted")
        self.assertContains(page, self.team.team_id)
        self.assertContains(page, "Alpha")
        self.assertContains(page, "Student 2")  # leader
        self.assertContains(page, "Student 3")  # original uploader
        self.assertEqual(Submission.objects.count(), 1)

    def test_one_submission_many_versions(self):
        self.verify(self.team)
        self.upload("E2100001")
        self.upload("E2100002", content=make_pdf(3000))
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(list(Upload.objects.order_by("version").values_list("version", "uploaded_by_enrolment")),
                         [(1, "E2100001"), (2, "E2100002")])
        sub = Submission.objects.get()
        self.assertEqual(sub.first.uploaded_by_enrolment, "E2100001")
        self.assertEqual(sub.latest.version, 2)

    def test_resubmission_can_be_disabled(self):
        self.milestone.allow_resubmission = False
        self.milestone.save()
        self.verify(self.team)
        self.upload("E2100001")
        page = self.client.get(reverse("submit_upload"))
        self.assertContains(page, "Replacements are switched off")
        self.upload("E2100002")
        self.assertEqual(Upload.objects.count(), 1)

    def test_file_validation(self):
        self.verify(self.team)
        cases = [
            ("notes.txt", b"hello", "Only PDF or Word"),
            ("evil.exe", b"MZ\x90\x00", "Only PDF or Word"),
            ("fake.pdf", b"this is plain text pretending", "valid PDF"),
            ("fake.docx", b"plain text", "valid Word"),
            ("zip.docx", b"PK\x03\x04garbage", "corrupted"),
            ("empty.pdf", b"", "empty"),
        ]
        for name, content, expected in cases:
            resp = self.upload("E2100001", content=content, name=name) if content else self.client.post(
                reverse("submit_upload"), {"enrolment": "E2100001", "report": SimpleUploadedFile(name, b"")})
            self.assertEqual(resp.status_code, 200, name)
            self.assertContains(resp, expected, msg_prefix=name)
        self.assertFalse(Upload.objects.exists())

    def test_file_size_limit(self):
        self.milestone.max_file_mb = 1
        self.milestone.save()
        self.verify(self.team)
        resp = self.upload("E2100001", content=make_pdf(1024 * 1024 + 10))
        self.assertContains(resp, "1 MB limit")

    def test_accepts_docx_and_doc(self):
        self.verify(self.team)
        self.assertEqual(self.upload("E2100001", make_docx(), "r.docx").status_code, 302)
        self.assertEqual(self.upload("E2100001", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 100, "r.doc").status_code, 302)
        self.assertEqual(Upload.objects.count(), 2)

    def test_deadline_blocks_then_extension_allows(self):
        self.milestone.deadline = timezone.now() - timedelta(hours=1)
        self.milestone.save()
        self.verify(self.team)
        self.assertContains(self.client.get(reverse("submit_upload")), "Closed")
        self.assertContains(self.upload("E2100001"), "deadline has passed")
        self.assertFalse(Upload.objects.exists())

        Extension.objects.create(milestone=self.milestone, team=self.team, new_deadline=timezone.now() + timedelta(days=1))
        self.assertEqual(self.upload("E2100001").status_code, 302)
        self.assertFalse(Upload.objects.get().is_late)

    def test_single_student_extension_only_helps_that_student(self):
        self.milestone.deadline = timezone.now() - timedelta(hours=1)
        self.milestone.save()
        Extension.objects.create(milestone=self.milestone, enrolment="e2100003", new_deadline=timezone.now() + timedelta(days=1))
        self.verify(self.team)
        self.assertContains(self.upload("E2100001"), "deadline has passed")
        self.assertEqual(self.upload("E2100003").status_code, 302)

    def test_late_uploads_are_flagged_when_allowed(self):
        self.milestone.deadline = timezone.now() - timedelta(hours=1)
        self.milestone.allow_late = True
        self.milestone.save()
        self.verify(self.team)
        self.assertEqual(self.upload("E2100001").status_code, 302)
        self.assertTrue(Upload.objects.get().is_late)

    def test_extending_for_everyone_by_moving_the_milestone_deadline(self):
        self.milestone.deadline = timezone.now() - timedelta(hours=1)
        self.milestone.save()
        self.verify(self.team)
        self.assertContains(self.upload("E2100001"), "deadline has passed")
        self.milestone.deadline = timezone.now() + timedelta(days=2)
        self.milestone.save()
        self.assertEqual(self.upload("E2100001").status_code, 302)

    def test_effective_deadline_is_the_latest(self):
        later = timezone.now() + timedelta(days=10)
        Extension.objects.create(milestone=self.milestone, team=self.team, new_deadline=timezone.now() + timedelta(days=5))
        Extension.objects.create(milestone=self.milestone, enrolment="E2100001", new_deadline=later)
        self.assertEqual(effective_deadline(self.milestone, self.team, "E2100001"), later)
        self.assertLess(effective_deadline(self.milestone, self.team, "E2100002"), later)

    def test_only_one_active_milestone(self):
        second = Milestone.objects.create(name="M3", slug="m3", deadline=timezone.now() + timedelta(days=9))
        self.milestone.refresh_from_db()
        self.assertFalse(self.milestone.is_active)
        self.assertEqual(Milestone.get_active(), second)

    def test_template_link_placeholder_and_link(self):
        self.assertContains(self.client.get(reverse("home")), "Template link coming soon")
        self.milestone.template_url = "https://example.com/template"
        self.milestone.save()
        self.assertContains(self.client.get(reverse("home")), "https://example.com/template")

    def test_uploads_are_not_publicly_downloadable(self):
        self.verify(self.team)
        self.upload("E2100001")
        up = Upload.objects.get()
        url = reverse("download_upload", args=[up.pk])
        self.assertEqual(self.client.get(url).status_code, 302)  # redirected to admin login
        staff = get_user_model().objects.create_user("staff", password="pw-for-tests-123", is_staff=True)
        self.client.force_login(staff)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(b"".join(resp.streaming_content).startswith(b"%PDF"))


class AdminTests(PortalCase):
    def setUp(self):
        super().setUp()
        self.team, _ = self.register("Alpha", (1, 2), leader=0)
        self.team2, _ = self.register("Beta", (5, 6), leader=1, client=self.client_class())
        self.verify(self.team)
        self.upload("E2100002")
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "pw-for-tests-123")
        self.client.force_login(self.admin)

    def test_pages_render(self):
        for name in ["admin:index", "admin:portal_team_changelist", "admin:portal_milestone_changelist",
                     "admin:portal_submission_changelist", "admin:portal_extension_changelist",
                     "admin:portal_auditlog_changelist", "admin:portal_batch_changelist",
                     "admin:portal_siteconfig_changelist"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("admin:portal_team_change", args=[self.team.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:portal_extension_add")).status_code, 200)

    def test_dashboard_numbers(self):
        page = self.client.get(reverse("admin:index"))
        self.assertEqual(page.context["teams"], 2)
        self.assertEqual(page.context["submitted"], 1)
        self.assertEqual(page.context["pending"], 1)

    def test_filters_and_search(self):
        url = reverse("admin:portal_team_changelist")
        self.assertContains(self.client.get(url + "?report=no"), "Beta")
        self.assertNotContains(self.client.get(url + "?report=no"), "Alpha</a>")
        self.assertContains(self.client.get(url + "?q=E2100006"), "Beta")

    def test_extend_everyone_action(self):
        before = self.milestone.deadline
        self.client.post(reverse("admin:portal_milestone_changelist"),
                         {"action": "extend_all_2_days", "_selected_action": [self.milestone.pk]})
        self.milestone.refresh_from_db()
        self.assertEqual(self.milestone.deadline - before, timedelta(days=2))

    def test_grant_team_extension_action(self):
        url = reverse("admin:portal_team_changelist")
        resp = self.client.post(url, {"action": "grant_extension", "_selected_action": [self.team2.pk]})
        self.assertContains(resp, "Grant a deadline extension")
        new = (timezone.localtime() + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M")
        resp = self.client.post(url, {"action": "grant_extension", "_selected_action": [self.team2.pk],
                                      "apply": "1", "new_deadline": new, "reason": "illness"})
        self.assertEqual(resp.status_code, 302)
        ext = Extension.objects.get(team=self.team2)
        self.assertEqual(ext.reason, "illness")
        self.assertGreater(effective_deadline(self.milestone, self.team2), self.milestone.deadline)

    def test_extension_needs_team_or_enrolment(self):
        resp = self.client.post(reverse("admin:portal_extension_add"), {
            "milestone": self.milestone.pk, "new_deadline_0": "2030-01-01", "new_deadline_1": "10:00:00", "reason": "x"})
        self.assertEqual(resp.status_code, 200)  # form re-rendered with an error
        self.assertFalse(Extension.objects.exists())

    def test_exports(self):
        resp = self.client.post(reverse("admin:portal_milestone_changelist"),
                                {"action": "export_status", "_selected_action": [self.milestone.pk]})
        text = resp.content.decode("utf-8-sig")
        self.assertIn(self.team.team_id, text)
        members_csv = self.client.post(reverse("admin:portal_team_changelist"),
                                       {"action": "export_members", "_selected_action": [self.team.pk]}).content.decode("utf-8-sig")
        self.assertIn("E2100001", members_csv)

        resp = self.client.post(reverse("admin:portal_milestone_changelist"),
                                {"action": "download_reports", "_selected_action": [self.milestone.pk]})
        self.assertEqual(resp["Content-Type"], "application/zip")
        zf = zipfile.ZipFile(io.BytesIO(b"".join(resp.streaming_content)))
        names = zf.namelist()
        self.assertIn("_manifest.csv", names)
        self.assertTrue(any(n.startswith(self.team.team_id) and n.endswith(".pdf") for n in names))

    def test_reset_pin_and_reminders(self):
        url = reverse("admin:portal_team_changelist")
        mail.outbox.clear()
        self.client.post(url, {"action": "reset_pin", "_selected_action": [self.team2.pk]})
        self.team2.refresh_from_db()
        self.assertEqual(len(mail.outbox), 1)
        mail.outbox.clear()
        self.client.post(reverse("admin:portal_milestone_changelist"),
                         {"action": "remind_pending", "_selected_action": [self.milestone.pk]})
        self.assertEqual(len(mail.outbox), 1)  # only Beta hasn't submitted
        self.assertIn(self.team2.name, mail.outbox[0].body)


class MiscTests(PortalCase):
    def test_public_pages(self):
        for name in ["home", "register", "submit_lookup", "team_edit_login", "team_find", "healthz"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get("/nope/").status_code, 404)

    def test_bootstrap_command_is_idempotent(self):
        from django.core.management import call_command
        call_command("bootstrap")
        call_command("bootstrap")
        self.assertEqual(SiteConfig.objects.count(), 1)


class SeedDemoTests(PortalCase):
    def test_refuses_without_debug_and_works_with_force(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("seed_demo")
        call_command("seed_demo", "--force", "--teams", "6", stdout=io.StringIO())
        self.assertEqual(Team.objects.filter(members__enrolment__startswith="DEMO").distinct().count(), 6)
        self.assertEqual(Submission.objects.count(), 3)
        call_command("seed_demo", "--force", "--clear", stdout=io.StringIO())
        self.assertFalse(Team.objects.exists())


class EmailBehaviourTests(PortalCase):
    @override_settings(EMAIL_RECIPIENTS="leader")
    def test_leader_only_mode_sends_one_email_per_team_event(self):
        team, pin = self.register("Alpha", (1, 2, 3), leader=1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["s2@example.com"])
        mail.outbox.clear()
        self.verify(team)
        self.upload("E2100001")
        self.assertEqual([m.to for m in mail.outbox], [["s2@example.com"]])

    @override_settings(EMAIL_BACKEND="portal.tests.BrokenBackend")
    def test_mail_failure_never_breaks_the_student_flow_and_is_logged(self):
        team, pin = self.register("Alpha", (1,))
        self.assertTrue(Team.objects.filter(name="Alpha").exists())
        from .models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="email_failed").exists())

    @override_settings(EMAIL_ASYNC=True)
    def test_async_mode_delivers_in_background(self):
        import time
        self.register("Alpha", (1,))
        for _ in range(50):
            if mail.outbox:
                break
            time.sleep(0.1)
        self.assertEqual(len(mail.outbox), 1)


from django.core.mail.backends.base import BaseEmailBackend  # noqa: E402


class BrokenBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise OSError("connection timed out")


@override_settings(EMAIL_ENABLED=False)
class EmailDisabledTests(PortalCase):
    """The default configuration: no email at all; the instructor resets PINs in the admin."""

    def test_nothing_is_ever_emailed(self):
        team, pin = self.register("Alpha", (1, 2))
        self.verify(team)
        self.upload("E2100001")
        self.assertEqual(mail.outbox, [])
        from .models import AuditLog
        self.assertFalse(AuditLog.objects.filter(action="email_failed").exists())

    def test_registration_page_explains_and_offers_a_download(self):
        team, pin = self.register("Alpha", (1, 2), leader=1)
        page = self.client.get(reverse("register_done"))
        self.assertContains(page, "send these by email")
        self.assertNotContains(page, "has also been emailed")
        card = self.client.get(reverse("register_card"))
        self.assertEqual(card.status_code, 200)
        self.assertIn("attachment", card["Content-Disposition"])
        text = card.content.decode()
        self.assertIn(team.team_id, text)
        self.assertIn(pin, text)
        self.assertIn("Student 2", text)

    def test_card_download_needs_a_fresh_registration_in_this_session(self):
        self.assertEqual(self.client_class().get(reverse("register_card")).status_code, 302)

    def test_lost_pin_page_points_to_the_instructor_and_cannot_reset(self):
        team, pin = self.register("Alpha", (1,))
        page = self.client.get(reverse("team_find"))
        self.assertContains(page, "Ask your instructor")
        resp = self.client.post(reverse("team_find"), {"enrolment": "E2100001", "email": "s1@example.com"})
        self.assertContains(resp, "Ask your instructor")
        team.refresh_from_db()
        self.assertTrue(team.check_pin(pin))
        self.assertEqual(mail.outbox, [])

    def test_wrong_pin_and_lockout_messages_mention_the_instructor(self):
        team, pin = self.register("Alpha", (1,))
        wrong = "000000" if pin != "000000" else "111111"
        r = self.client.post(reverse("submit_lookup"), {"team_id": team.team_id, "pin": wrong})
        self.assertContains(r, "Ask your instructor to reset it")
        for _ in range(10):
            self.client.post(reverse("submit_lookup"), {"team_id": team.team_id, "pin": wrong})
        r = self.client.post(reverse("submit_lookup"), {"team_id": team.team_id, "pin": pin})
        self.assertContains(r, "locked")
        self.assertContains(r, "Ask your instructor to reset your PIN")

    def test_admin_pin_reset_shows_pin_and_lifts_lockout(self):
        team, pin = self.register("Alpha", (1, 2))
        wrong = "000000" if pin != "000000" else "111111"
        for _ in range(10):
            self.client.post(reverse("submit_lookup"), {"team_id": team.team_id, "pin": wrong})
        staff = get_user_model().objects.create_superuser("root", "r@example.com", "pw-for-tests-123")
        admin_client = self.client_class()
        admin_client.force_login(staff)
        resp = admin_client.post(reverse("admin:portal_team_changelist"),
                                 {"action": "reset_pin", "_selected_action": [team.pk]}, follow=True)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertEqual(len(msgs), 1)
        self.assertIn(team.team_id, msgs[0])
        new_pin = msgs[0].rsplit(" ", 1)[1]
        self.assertTrue(len(new_pin) == 6 and new_pin.isdigit())
        self.assertEqual(mail.outbox, [])
        # the student can use the new PIN straight away (lockout cleared); the old one is dead
        self.verify_with(team, new_pin)
        other = self.client_class()
        r = other.post(reverse("submit_lookup"), {"team_id": team.team_id, "pin": pin})
        self.assertContains(r, "doesn&#x27;t match")
        from .models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="pin_reset_by_admin", team=team).exists())

    def verify_with(self, team, pin):
        resp = self.client.post(reverse("submit_lookup"), {"team_id": team.team_id, "pin": pin})
        self.assertRedirects(resp, reverse("submit_upload"), fetch_redirect_response=False)

    def test_reminder_actions_are_hidden_while_email_is_off(self):
        self.register("Alpha", (1,))
        staff = get_user_model().objects.create_superuser("root", "r@example.com", "pw-for-tests-123")
        self.client.force_login(staff)
        for name, action in [("admin:portal_team_changelist", "remind"), ("admin:portal_milestone_changelist", "remind_pending")]:
            choices = [c[0] for c in self.client.get(reverse(name)).context["action_form"].fields["action"].choices]
            self.assertNotIn(action, choices, name)
        self.assertIn("reset_pin", [c[0] for c in self.client.get(reverse("admin:portal_team_changelist")).context["action_form"].fields["action"].choices])

    def test_other_pages_do_not_promise_emails(self):
        team, pin = self.register("Alpha", (1,))
        self.verify(team)
        self.upload("E2100001")
        done = self.client.get(reverse("submit_done"))
        self.assertNotContains(done, "was emailed")
        for name in ["home", "submit_lookup", "team_edit_login"]:
            self.assertNotContains(self.client.get(reverse(name)), "emailed", msg_prefix=name)


class BatchRangeTests(PortalCase):
    def test_expand(self):
        from portal import batches
        names = batches.expand("B1-B3, b65-67, A9, B2")
        self.assertEqual(names, ["B1", "B2", "B3", "B65", "B66", "B67", "A9"])
        for bad in ["", "B5-B1", "16", "B1-C4", "B1-B9999"]:
            with self.assertRaises(ValueError, msg=bad):
                batches.expand(bad)

    def test_admin_add_range_and_export_all(self):
        admin_user = get_user_model().objects.create_superuser("root", "r@example.com", "pw-for-tests-123")
        self.client.force_login(admin_user)
        url = reverse("admin:portal_batch_add_range")
        self.assertEqual(self.client.get(url).status_code, 200)
        pat = r'^B(\d+)$'
        before_b = Batch.objects.filter(name__regex=pat).count()
        overlap = Batch.objects.filter(name__in=[f'B{n}' for n in list(range(1, 17)) + list(range(65, 81))]).count()
        self.client.post(url, {"ranges": "B1-B16, B65-B80", "max_team_size": 5, "is_active": "on"})
        self.assertEqual(Batch.objects.filter(name__regex=r'^B(\d+)$').count() - before_b, 32 - overlap)
        self.assertTrue(Batch.objects.filter(name="B80", max_team_size=5, is_active=True).exists())
        self.assertFalse(Batch.objects.filter(name="B17").exists())
        self.client.post(url, {"ranges": "B1-B16", "max_team_size": 5})  # re-run: no duplicates
        self.assertEqual(Batch.objects.filter(name="B1").count(), 1)
        self.assertEqual(self.client.get(reverse("admin:portal_team_export_all")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:portal_team_changelist")).status_code, 200)


class LinkPreviewTests(PortalCase):
    def test_meta_tags(self):
        cfg = SiteConfig.get()
        cfg.site_title, cfg.course_name = "Demo Team Portal", "Demo Course"
        cfg.save()
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn('property="og:title" content="Demo Team Portal', html)
        self.assertIn('name="description" content="Register your team and submit your project reports for Demo Course."', html)
        cfg.description = "Custom blurb"
        cfg.save()
        self.assertIn('property="og:description" content="Custom blurb"', self.client.get(reverse("home")).content.decode())
