"""Fill a LOCAL database with fake teams so the dashboard, filters and exports have something to show."""
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from portal import ids
from portal.models import Batch, Extension, Member, Milestone, Team
from portal.services import save_report

NAMES = ["Byte Builders", "Null Pointers", "Stack Overflowers", "Git Happens", "Cache Me Outside",
         "Merge Conflict", "Tab Spaces", "Segfault Squad", "Lambda Lions", "Pixel Pioneers",
         "Query Queens", "Loop Legends"]
DEMO_PREFIX = "DEMO"


class Command(BaseCommand):
    help = "Create fake demo teams (local testing only). Use --clear to remove them again."

    def add_arguments(self, parser):
        parser.add_argument("--teams", type=int, default=10)
        parser.add_argument("--clear", action="store_true", help="Delete demo teams and exit.")
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG off (not for production).")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["force"]:
            raise CommandError("seed_demo is for local testing only (DEBUG is off). Use --force if you really mean it.")

        demo_teams = Team.objects.filter(members__enrolment__startswith=DEMO_PREFIX).distinct()
        if opts["clear"]:
            count = demo_teams.count()
            demo_teams.delete()
            self.stdout.write(f"Removed {count} demo team(s).")
            return
        if demo_teams.exists():
            self.stdout.write("Demo data already present (use --clear first to rebuild).")
            return

        batches = list(Batch.objects.filter(is_active=True)) or [Batch.objects.create(name="Batch 1", max_team_size=5)]
        milestone = Milestone.get_active()
        n_teams = min(opts["teams"], len(NAMES))
        serial = 0
        created = []
        for t in range(n_teams):
            batch = batches[t % len(batches)]
            size = 2 + (t % 4)  # 2..5
            team = Team(team_id=ids.generate(), name=NAMES[t])
            pin = "123456"
            team.set_pin(pin)
            team.save()
            for i in range(size):
                serial += 1
                Member.objects.create(
                    team=team, position=i, is_leader=(i == 0), batch=batch,
                    name=f"Demo Student {serial}", enrolment=f"{DEMO_PREFIX}{serial:04d}",
                    phone=f"90000{serial:05d}", email=f"demo{serial}@example.com",
                )
            created.append(team)

        submitted = 0
        if milestone:
            for team in created[: n_teams // 2]:  # first half have submitted
                member = team.members.first()
                pdf = SimpleUploadedFile("demo.pdf", b"%PDF-1.4\n" + b"demo " * 400 + b"\n%%EOF")
                save_report(milestone, team, member, pdf)
                submitted += 1
            if len(created) > submitted:  # one pending team gets an extension
                Extension.objects.create(milestone=milestone, team=created[-1],
                                         new_deadline=milestone.deadline + timedelta(days=2),
                                         reason="demo extension")

        self.stdout.write(self.style.SUCCESS(
            f"Created {len(created)} demo teams ({submitted} with a submitted report). Edit PIN for all of them: 123456"))
        self.stdout.write("Try these Team IDs on the Submit page:")
        for team in created[:3] + created[-2:]:
            state = "submitted" if team.submissions.exists() else "not submitted"
            self.stdout.write(f"  {team.team_id}  {team.name}  ({state}; enrolment of leader: {team.leader.enrolment})")
