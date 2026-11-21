# Team Portal

A small, self-hosted web app for **registering student teams** and **collecting one report per team**.

- Students register a team (size → member rows → review → unique **Team ID** + team PIN).
- To submit, they must sign in with the team's **Team ID and PIN** (so only team members can) and give an enrolment number that belongs to that team.
- One report per team (PDF / Word). Once any teammate uploads, the team shows as submitted — with team ID,
  name, leader and who uploaded first.
- Deadline per milestone, with extensions for a team, a single student, or everyone.
- Admin dashboard, CSV exports, ZIP download of all reports, reminder emails.

Built with Django + SQLite (no external services). Runs as one Docker container.

## Getting started

**See [SETUP.md](SETUP.md)** for step-by-step instructions:

- **Try it locally** in one command, no Docker needed: `python dev.py demo`
- **Deploy on a server** with Docker (HTTPS, email, backups, updates, troubleshooting)

## First-time setup in the admin

Log in at `/admin/`, then:

1. **Batches** – rename the placeholder batches and set each batch's *max team size* (e.g. 5). A mixed team is limited by its strictest batch.
2. **Milestones → Milestone 2 Report** – set the **deadline**, paste the **template link** when you have it (until then students see "Template link coming soon"), and choose whether late uploads / re-uploads are allowed.
3. **Portal settings** – site title, course name, support email, optional banner, min team size.
4. The dashboard shows a *setup checklist* for anything still missing (template link, email, placeholder batches).

## Running it

| I want to… | Do this |
|---|---|
| Extend for **everyone** | Milestones → select → *Extend deadline for EVERYONE by…*, or just edit the deadline |
| Extend for **certain teams** | Teams → select → *Grant a deadline extension to selected teams…* |
| Extend for **one student** | Extensions → Add → enter their enrolment number |
| See who hasn't submitted | Teams → filter *Not submitted* (also *Submitted late*) |
| Remind them | Teams/Milestones → *Email a reminder…* |
| Download everything | Milestones → *Download all reports (ZIP)* (latest versions + `_manifest.csv`) |
| Spreadsheet of status | Milestones → *Export submission status (CSV)* |
| Fix a team / reset a PIN | Teams → open it, or action *Reset team PIN* |
| Let a team upload again | Delete its Submission (Submissions) — or leave *allow re-upload* on |
| Next milestone | Add a Milestone and tick *Active* (only one is active; students always submit to it) |

Deadlines are enforced on the server (default time zone `Asia/Kolkata`, change with `TIME_ZONE`).
The effective deadline for an upload is the **latest** of: milestone deadline, the team's extension, the student's extension.

## Configuration

All via environment variables — see `.env.example`. The important ones: `SECRET_KEY`, `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `SITE_URL`, `TEAM_ID_PREFIX`, `ENROLMENT_REGEX`, `ALLOWED_EMAIL_DOMAINS`, `EMAIL_*`.
Without `EMAIL_*`, emails are printed to the container log (`docker compose logs web`).

## Design notes

- **Team IDs** look like `TM-K7M-2QX`: 5 random characters plus a check character, alphabet without look-alikes
  (0/O, 1/I/L). Single typos and swaps are rejected before the database is even queried.
- **The PIN** (6 digits, hashed in the database) is needed to submit and to edit. 10 wrong PINs lock a team for an hour; any member can get a new PIN emailed to the whole team via “Lost your Team ID or PIN?” (once per 15 minutes).
- **Uploads** are validated by extension, size and file content, stored outside any public path, and downloadable only by staff.
  One `Submission` row per (milestone, team) is enforced by a database constraint, so simultaneous uploads can't create duplicates.
- **Rate limiting** on lookups is deliberately generous (a campus often shares one public IP).
- Enrolment numbers are unique across teams (a student can't be in two teams).

## Development

`python dev.py` runs the site locally; `python dev.py test` runs the tests. Backups and restores are covered in [SETUP.md](SETUP.md).
