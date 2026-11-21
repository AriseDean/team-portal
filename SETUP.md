# Setup guide

Two parts: **A. try it on your computer**, then **B. deploy it on a server**.
Do A first; B reuses everything you learn there.

---

## A. Try it locally (about 5 minutes)

You need **Python 3.10 or newer**. Check with `python --version` (on Windows try `py --version`).
No Docker, no database server, nothing else to install.

### 1. Start it

Open a terminal in the project folder (the one containing `dev.py`) and run:

```bash
python dev.py demo        # Windows: py dev.py demo
```

The first run creates a private `.venv/`, installs the dependencies, sets up the database, loads some fake demo
teams, and starts the site. Later runs take a second.

| What | Where |
|---|---|
| Student site | http://127.0.0.1:8000/ |
| Admin | http://127.0.0.1:8000/admin/ (login `admin` / `admin12345`) |
| Email | Off by default (nothing is sent). To try it: `EMAIL_ENABLED=1 python dev.py`; emails then print in the terminal |

Stop with `Ctrl+C`. Use `python dev.py` (without `demo`) for an empty site.

### 2. A guided tour (what to check)

1. **Register a team.** Home → *Register a team*. Choose a team size, fill in the member cards, tick the ★ leader,
   review, confirm. You get a Team ID and PIN on screen. Try *Download details (.txt)* too.
2. **Submit a report.** *Submit report* → enter the Team ID (lower-case is fine) **and PIN** → your enrolment number → upload any PDF or Word file.
3. **See the "already submitted" view.** Open a **private/incognito window**, sign in with the same Team ID and PIN, and use a *different*
   teammate's enrolment number. You should see the team ID, name, leader and who uploaded first.
4. **Try the failure cases.** An unregistered Team ID (e.g. `TM-ABC-234`), a wrong PIN, a made-up enrolment number, a `.txt` file renamed to `.pdf`.
   Then try the lost-PIN route: in the admin, *Teams* → tick a team → action *Reset team PIN*. The new PIN appears at the top of the page, and the old PIN stops working.
5. **Deadlines.** In the admin: *Milestones → Milestone 2 Report*, set the deadline to a minute ago and save. Submissions are now blocked.
   Then try each way of extending: tick *Allow late uploads*; or *Teams →* select a team → action *Grant a deadline extension…*;
   or *Milestones →* action *Extend deadline for EVERYONE*.
6. **Admin tools.** Look at the dashboard counts, filter *Teams → Not submitted*, and try the *Export submission status (CSV)* and
   *Download all reports (ZIP)* actions on the milestone list.
7. **Edit a team.** *Edit team* → Team ID + PIN. The demo teams all use PIN `123456`, which also works on the Submit page.
8. **Template link.** Paste any URL into the milestone's *Template url* and see the button appear on the home and submit pages.

### 3. Handy commands

```bash
python dev.py test        # run the automated tests (47 of them)
python dev.py reset       # delete the local database and uploads; start fresh
python dev.py demo        # start with fake demo teams loaded (only added once)
```

Local data lives in `data/` (git-ignored). Demo students have enrolment numbers starting `DEMO`, and the demo loader refuses to run unless debug mode is on, so it can't pollute a real server.

### Optional: rehearse the Docker setup on your computer

Install Docker Desktop, then create a `.env` file with exactly this:

```
SECRET_KEY=local-test-only-change-me
ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost:8000
SITE_URL=http://localhost:8000
HTTPS=0
TRUST_PROXY=0
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin12345
```

```bash
docker compose up --build        # site at http://localhost:8000, admin at /admin/
docker compose logs -f web       # application log
docker compose down              # stop (data is kept)   |   docker compose down -v  (also deletes data)
```

---

## B. Deploy on a server

### What you need

- A Linux server (1 vCPU / 1 GB RAM is plenty for 600 students) you can SSH into.
- A (sub)domain pointing at it, e.g. `teams.example.com` (an `A` record to the server's IP).
- Ports **80 and 443** reachable. If something else already uses them, use your existing reverse proxy (option B below).
- Nothing else: email is switched off by default (see *Optional: turn email on later* at the end if you want it).

### 1. Install Docker (once)

On Ubuntu/Debian:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # log out and in again afterwards
docker compose version             # should print a version
```

### 2. Put the project on the server

Either `git clone <your-repo-url> /opt/team-portal`, or copy the folder from your computer:

```bash
rsync -av --exclude .venv --exclude data --exclude .env ./ user@server:/opt/team-portal/
```

### 3. Configure

```bash
cd /opt/team-portal
cp .env.example .env
nano .env
```

Fill in at least these (everything else has sensible defaults; email stays off):

| Setting | Value |
|---|---|
| `SECRET_KEY` | A long random string: `openssl rand -base64 48` |
| `ALLOWED_HOSTS` | `teams.example.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://teams.example.com` (include `https://`) |
| `SITE_URL` | `https://teams.example.com` |
| `DOMAIN` | `teams.example.com` (only for the bundled Caddy) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Your first admin login, created on first start only |
| `ALLOWED_EMAIL_DOMAINS` | Optional: e.g. `example.edu` to accept only institute addresses |

Keep `HTTPS=1` and `TRUST_PROXY=1` (the defaults in `.env.example`). Set `TIME_ZONE` if you're not in `Asia/Kolkata`.
`.env` holds secrets: never commit or share it (it's already git-ignored).

### 4. Choose how HTTPS is provided

**Option A: bundled Caddy (simplest).** Free ports 80/443, DNS in place, then:

```bash
docker compose --profile caddy up -d --build
```

Caddy gets and renews the certificate automatically.

**Option B: you already run nginx/Traefik/Caddy.** Start only the app (it listens on `127.0.0.1:8000`):

```bash
docker compose up -d --build
```

and proxy to it. nginx example:

```nginx
server {
    server_name teams.example.com;
    client_max_body_size 25m;                       # report uploads
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

then `sudo certbot --nginx -d teams.example.com` for HTTPS.

### 5. Verify it's up

```bash
docker compose ps                                  # web should be "healthy"
docker compose logs --tail 50 web                  # look for "Listening at"
curl -I https://teams.example.com/healthz          # HTTP 200
```

Open `https://teams.example.com/admin/` and log in.

### 6. First-time configuration (in the admin)

1. **Batches:** *Batches → Add batches by range*, type e.g. `B1-B16, B65-B80` (letters + number, ranges and commas; existing ones are skipped), then delete the placeholder "Batch 1-3" and set max team size per batch.
2. **Milestones → Milestone 2 Report:** real deadline, template link (when you have it), late/re-upload options.
3. **Portal settings:** title, course name, support email, optional banner.
4. **Change your admin password** (top right) and delete the `ADMIN_PASSWORD` line from `.env` (it's only read on first start).

### 7. Dress rehearsal

Register a test team (save the Team ID and PIN shown), submit a file, check it appears in the admin, download it. Then delete the test team
(*Admin → Teams → delete*). Everything the students will do, you've now done once.

### 8. Pre-launch checklist

- [ ] Deadline, batches and template link set; dashboard "setup checklist" is empty
- [ ] Test registration + submission worked over `https://`
- [ ] Backup cron job added (below) and you've tested a restore at least once
- [ ] Test data deleted
- [ ] Share the link: `https://teams.example.com/` and tell students to **save their Team ID + PIN** when they register (nothing is emailed; both are needed to submit)

### Day-to-day operations

```bash
cd /opt/team-portal
docker compose logs -f web                     # live logs
docker compose restart web

# update to a newer version
git pull                                        # or copy the new files over
docker compose up -d --build                    # add --profile caddy if you use it; data is kept

# admin accounts
docker compose exec web python manage.py changepassword admin
docker compose exec web python manage.py createsuperuser
```

**A student lost their Team ID or PIN.** In the admin go to *Teams*, search by team name, student name or enrolment number
(the Team ID is in the list), tick the team, choose the action **Reset team PIN** and press Go. The new PIN appears at the top of
the page: tell the student (the old PIN stops working, and any "too many wrong PINs" lock is lifted). PINs are stored hashed, so an
existing PIN can't be looked up, only replaced.

**Backups.** Take a consistent database copy (kept in the data volume, newest 14 retained) and add it to cron:

```bash
docker compose exec -T web python manage.py backup
# crontab -e   →   0 2 * * * cd /opt/team-portal && docker compose exec -T web python manage.py backup
```

Also copy the whole data volume (database **and** uploaded reports) off the server now and then:

```bash
docker run --rm -v team-portal_portal_data:/data -v "$PWD":/out alpine tar czf /out/portal-$(date +%F).tgz /data
```

**Restore a database backup** (pick a file from `docker compose exec web ls /data/backups`):

```bash
docker compose stop web
docker compose run --rm --no-deps --entrypoint sh web -c \
  "cp /data/backups/db-YYYYMMDD-HHMMSS.sqlite3 /data/db.sqlite3 && rm -f /data/db.sqlite3-wal /data/db.sqlite3-shm"
docker compose start web
```

**After the course.** Download the ZIP and CSVs you need, then delete the data (Admin → Teams → select all → delete, or
`docker compose down -v` to wipe the whole volume). The portal holds students' phone numbers and emails, so don't keep it longer than needed.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| **403 "CSRF verification failed"** | `CSRF_TRUSTED_ORIGINS` must contain the exact site URL, including `https://`. On plain HTTP (testing) set `HTTPS=0`. |
| **400 "Bad Request"** | The hostname isn't in `ALLOWED_HOSTS`. |
| Forms/login don't stick on plain HTTP | Cookies are "secure-only" while `HTTPS=1`. Use HTTPS, or `HTTPS=0` for local testing only. |
| Upload fails with "413 Request Entity Too Large" | Your proxy's body limit: nginx `client_max_body_size 25m;` (bundled Caddy already allows 25 MB). |
| Page has no styling | The image is stale: `docker compose up -d --build`. |
| Container keeps restarting | `docker compose logs web`. Usually a missing `SECRET_KEY`. |
| Emails not arriving | Email is off unless `EMAIL_ENABLED=1`. If you turned it on, see *Optional: turn email on later* (DigitalOcean blocks ports 25/465/587; use 2525). |
| Everyone shows the same IP / "too many attempts" | Behind a proxy set `TRUST_PROXY=1` and make sure the proxy sends `X-Forwarded-For`. |
| Port 8000 already in use | Set `PORT=8001` in `.env`, then update your proxy to match. |
| Forgot the admin password | `docker compose exec web python manage.py changepassword admin` |

---

## C. Optional: turn email on later

Email is **off by default**. When you want it (registration confirmations, submission receipts, reminders, self-service PIN recovery), set `EMAIL_ENABLED=1` and the `EMAIL_*` values below.

**DigitalOcean blocks outbound SMTP on ports 25, 465 and 587 for every Droplet** (that includes Gmail's and most
institutes' SMTP servers), and it won't unblock them for new accounts. Two practical consequences:

- Don't try to send mail directly from the server or through `smtp.gmail.com`; the connection will just hang.
- Use a transactional-email relay that accepts connections on **port 2525** (or its HTTPS API). The free plans below are plenty for a class.

Once enabled, emails go out in the background with a 10-second timeout, a failure never blocks a student, and failures show up
under *Recent activity* on the admin dashboard. Extra things that appear when email is on: *Email a reminder* admin actions, a
self-service "Lost your Team ID or PIN?" form for students, and confirmation/receipt emails.

**Recommended: Brevo free plan** (300 emails/day, no card, SMTP on port 2525).

1. Create a free account at brevo.com.
2. **Authenticate your domain** (Senders, Domains → Domains → add `teams.example.com` or your main domain). Brevo shows a few DNS records
   (SPF/DKIM/DMARC); add them where your DNS lives (on DigitalOcean: *Networking → Domains*). This is what keeps the mails out of spam.
3. *SMTP & API → SMTP* → generate an **SMTP key**. Your SMTP login is shown on the same page. (The password is the SMTP key, not your account password or the API key.)
4. Put this in `.env`:

   ```
   EMAIL_ENABLED=1
   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
   EMAIL_HOST=smtp-relay.brevo.com
   EMAIL_PORT=2525
   EMAIL_USE_TLS=1
   EMAIL_HOST_USER=<your SMTP login>
   EMAIL_HOST_PASSWORD=<your SMTP key>
   DEFAULT_FROM_EMAIL=Team Portal <noreply@your-domain.com>
   EMAIL_RECIPIENTS=leader
   ```

5. From the server, check the port is reachable, then send a real test mail:

   ```bash
   nc -zv smtp-relay.brevo.com 2525                 # "succeeded" = not blocked
   docker compose up -d                              # reload .env (restarts the app with the new settings)
   docker compose exec web python manage.py sendtestemail you@example.com
   ```

**Mind the daily cap.** A free plan of 300/day counts every recipient. With ~600 students (≈150 teams), `EMAIL_RECIPIENTS=all`
means ≈600 registration emails plus ≈600 receipts, which can exceed 300 on a busy day. `EMAIL_RECIPIENTS=leader` sends one email per team
event (≈150 per wave), comfortably inside the limit, and the leader shares the PIN with teammates. Use `all` only if your provider has a higher limit.

Other free options (limits change, so check their pricing pages): Mailjet (about 200/day), Mailgun (about 100/day, also port 2525), Resend (about 100/day, 3,000/month).
Anything that gives you an SMTP host, a username/password and a port other than 25/465/587 works with the same settings.
If a provider only offers the blocked ports, it can't be used from a DigitalOcean Droplet.

**Using your own dedicated server instead?** Possible, but I wouldn't: you'd need working SPF/DKIM/reverse-DNS and a clean IP to stay out of spam, and the
Droplet still couldn't reach your server on port 25/587. A relay is simpler and more reliable.
