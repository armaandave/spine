# Backups: nightly, verified and off-site

This page explains how Spine's production data is backed up every night, how to
check that the backups really work, and how to get everything back if the worst
happens. It is written for someone who has never set up backups before. You do
not need to understand Docker, Postgres or launchd in depth: every step says
what to type and what you should see.

**How long it takes:** about 45 minutes the first time. You can stop after
Step 2 and already have local backups. The later steps add off-site copies
(Cloudflare R2) and an alarm that tells you when a backup did not happen
(healthchecks.io).

**Where you type things:** every command on this page is typed into Terminal on
the **server Mac** (the one that runs the `spine`, `spine-db` and `spine-redis`
containers), not on your laptop. Open Terminal by pressing `Cmd+Space`, typing
`Terminal` and pressing Return. Each grey box is one or more commands: paste
the box, press Return, and wait for the prompt to come back before the next box.

## Contents

* [What you get](#what-you-get)
* [Before you start](#before-you-start)
* [Step 1: install the scripts and the settings file](#step-1-install-the-scripts-and-the-settings-file)
* [Step 2: take your first backup](#step-2-take-your-first-backup)
* [Step 3: prove it can be restored](#step-3-prove-it-can-be-restored)
* [Step 4: create the off-site storage (Cloudflare R2)](#step-4-create-the-off-site-storage-cloudflare-r2)
* [Step 5: install rclone and connect it to R2](#step-5-install-rclone-and-connect-it-to-r2)
* [Step 6: set up the alarm (healthchecks.io)](#step-6-set-up-the-alarm-healthchecksio)
* [Step 7: tell the backup about R2 and healthchecks.io](#step-7-tell-the-backup-about-r2-and-healthchecksio)
* [Step 8: run it by hand and check R2](#step-8-run-it-by-hand-and-check-r2)
* [Step 9: make it run every night](#step-9-make-it-run-every-night)
* [Every month: prove the backups work](#every-month-prove-the-backups-work)
* [Disaster recovery: getting everything back](#disaster-recovery-getting-everything-back)
* [Troubleshooting](#troubleshooting)
* [Reference](#reference)

---

## What you get

Every night at 3:30 am the server Mac does this, automatically:

```text
 3:30 am
   |
   |-- 1. takes the lock (one backup at a time), tells healthchecks.io "a backup is starting"
   |-- 2. checks free disk space, Docker Desktop, and that the database accepts connections
   |-- 3. dumps the Postgres database                  -> spine-db-<time>.dump
   |-- 4. zips the uploaded media (profile pictures)   -> spine-media-<time>.tar.gz
   |-- 5. reads both back to check they are complete    (a damaged file is thrown away;
   |                                                      a suspiciously empty one is flagged)
   |-- 6. keeps the first good backup of each month as a separate monthly copy
   |-- 7. uploads the new files to Cloudflare R2, and checks the upload
   |-- 8. only then deletes local nightly backups older than the newest 14
   `-- 9. tells healthchecks.io "success" with a one-line summary  (or "failed", with the reason)
```

If anything goes wrong, the script exits with an error and healthchecks.io
emails you. If the Mac is off or the script never runs, healthchecks.io emails
you too, because the "success" message never arrived.

### What is backed up, and where it goes

| What | How | On the server Mac | Off-site (R2) | Kept |
| --- | --- | --- | --- | --- |
| The **database** (users, diary, lists, ratings: everything you track) | `pg_dump` custom format, verified | `~/projects/spine-backups/nightly/spine-db-<time>.dump` | `spine-backups/nightly/` | last 14 nights locally, about 30 days off-site |
| The **media files** (users' uploaded profile pictures) | `.tar.gz` of the media volume, verified | `~/projects/spine-backups/nightly/spine-media-<time>.tar.gz` | `spine-backups/nightly/` | same |
| A **monthly copy** of both (the first good backup of each month) | the same files, hard-linked so they cost no extra space until the nightly copy is deleted | `~/projects/spine-backups/monthly/` | `spine-backups/monthly/` | newest 3 locally; off-site until you delete them (Step 4 suggests a 1-year rule) |
| A dump taken **before a deployment** | made by the deploy workflow's own backup step. **That workflow is only on the `codex/game-tracking-end-to-end` branch so far, not on `myapp-main`, so until it is merged and used, deployments are NOT protected this way** | `~/projects/spine-backups/pre-deploy-<run>-<attempt>.dump` | not uploaded | until you delete them |

So everything lives under one folder, `~/projects/spine-backups`. The nightly
files go into its `nightly` sub-folder and the monthly copies into `monthly`.

The `<time>` in a file name is **UTC** (that is what the `Z` at the end means),
written as year, month, day, `T`, hour, minute, second.
`spine-db-20260930T073001Z.dump` was made on 30 September 2026 at 07:30:01 UTC,
which is 3:30:01 am in New York (daylight saving time). UTC is used so that file
names always sort in time order. The log file shows your local time.

### What is NOT backed up (read this)

| Not backed up | Why | What to do |
| --- | --- | --- |
| `~/projects/spine/.env.production` (secret key, database password, API keys) | it holds secrets, so it is not copied around automatically | keep a copy in your password manager. Without it you must create new secrets and re-enter the API keys |
| The R2 access key and secret | they are secrets too | keep them in your password manager (you can also create new ones in Cloudflare at any time) |
| Redis (`spine-redis`) | it holds the cache and the queue of background jobs that are waiting. The cache refills by itself, but jobs that were waiting at the moment of a disaster are lost (for example an import a user had just started has to be started again) | nothing to restore; start such jobs again |
| The code and Docker images | the code is in GitHub and images are rebuilt on deploy | nothing |
| `~/.config/spine/backup.env` and `~/.config/rclone/rclone.conf` | they are your backup settings | easy to recreate by following this page again |

### What the scripts guarantee

* **A bad backup never replaces a good one.** The dump is written to a temporary
  file, read back in full to check that it is complete, and only then renamed
  into place. (Checking just the table of contents, which is what
  `pg_restore --list` does, does not notice a file that was cut off half way, so
  the script reads the whole file.)
* **"Valid but useless" backups raise the alarm.** A dump with no users in it, a
  dump that is much smaller than the biggest of the last week's healthy dumps, or
  a media archive with nothing in it is still kept (it is a valid file), but the
  run is reported as **failed** so you look at it, and no old backups are deleted
  that night. The size check compares with the biggest recent healthy dump, not
  just with yesterday's, so a slow leak of data, or one bad night followed by
  another, cannot make itself look normal. (If you deleted data on purpose, see
  the `sanity:` row in [Troubleshooting](#troubleshooting).)
* **Old backups are deleted last, and carefully.** Only after a fully successful
  run and after the off-site upload; only files named exactly like the pattern
  above; never the files made in this very run; and not at all if the Mac's clock
  looks wrong (for example stuck in the past). The monthly copies mean a long run
  of bad nights can never push every good backup out of the nightly window.
* **Nothing can hang forever.** Every Docker and rclone call has a time limit, the
  database dump gives up after 2 minutes of waiting for a table that a migration
  is holding (instead of waiting for as long as the migration lasts), the whole
  run has a limit of 3 hours, and if the job is stopped (shutdown,
  `launchctl bootout`) it cleans up within a couple of seconds instead of leaving
  half-written files behind. A closed Terminal window, or output piped into
  `head`, does not stop a backup half way either.
* **One backup at a time.** A second run that starts while one is running does
  nothing and says so (exit status 75, and no false alarm to healthchecks.io). The
  lock is a real operating-system file lock that every part of a run holds, so
  the computer itself drops it the moment the last part of the run is gone, even
  after `kill -9`: there is never a stale lock to clear by hand. If the nightly job
  is ever killed outright, what it was doing stops by itself within seconds and
  cleans up, and until then a new run is refused rather than running alongside it.
* **The media volume is found automatically** by asking the `spine` container
  where its media folder comes from, so nothing depends on a volume name. The
  database dump does not need the app container at all.
* **The off-site upload is verified** (size and checksum) and never deletes
  anything in R2. The R2 lifecycle rules (Step 4) do the expiring.
* **Nothing secret is ever printed** to the log, sent to healthchecks.io, or put
  on a command line where `ps` could show it. The scripts never read
  `.env.production` and never need the database password.
* **Anything you have not set up yet is skipped** with a `SKIP` line in the log,
  never an error. (Once R2 works you can make a missing off-site copy an error
  with `REQUIRE_OFFSITE`, Step 7.)

---

## Before you start

Check these five things on the server Mac.

**a) The site is running.** Type:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

You should see three lines whose names are `spine`, `spine-db` and
`spine-redis`, each with a status starting `Up`. If Docker says it cannot
connect, open Docker Desktop and wait until it says it is running.

**b) Docker Desktop starts by itself.** In Docker Desktop open *Settings* →
*General* and turn on *Start Docker Desktop when you sign in to your computer*.
Otherwise after a restart there will be no database to back up.

**c) The Mac does not go to sleep at night.** Backups run at 3:30 am. In *System
Settings* look under *Energy* (desktop Macs) or *Battery → Options* (laptops)
and switch on the option that stops the Mac sleeping automatically when the
display is off. (While a backup is running, the script keeps the Mac awake. If the
Mac *is* asleep at 3:30, the backup runs as soon as it wakes up. If it is switched
off, that night is skipped and healthchecks.io will tell you.)

**d) You have the scripts.** Type:

```bash
ls ~/projects/spine/scripts/backup-production.sh
```

It should print the file name. If it says "No such file", the scripts have not
reached the server yet: update the repository with
`cd ~/projects/spine && git pull` once the change that adds them has been merged
into the branch you have checked out.

**e) Two free accounts** (only needed from Step 4 on):
[Cloudflare](https://dash.cloudflare.com) (you already use it for the API
domain) and [healthchecks.io](https://healthchecks.io).

---

## Step 1: install the scripts and the settings file

The nightly job should keep working even if you later switch the repository to a
different branch, so the scripts are copied to a folder that git never touches.
Paste this whole box:

```bash
mkdir -p ~/.local/share/spine-backup ~/.config/spine
cp ~/projects/spine/scripts/backup-production.sh ~/projects/spine/scripts/restore-check.sh ~/.local/share/spine-backup/
chmod 755 ~/.local/share/spine-backup/backup-production.sh ~/.local/share/spine-backup/restore-check.sh
cp -n ~/projects/spine/scripts/backup.env.example ~/.config/spine/backup.env
chmod 600 ~/.config/spine/backup.env
```

What each line does:

1. Creates the two folders (one for the scripts, one for your settings).
2. Copies the two scripts into the first folder.
3. Makes them runnable.
4. Creates your settings file from the example (`-n` means an existing file is never overwritten).
5. Makes the settings file readable only by you, because it will hold a secret URL. (The scripts warn you if it is ever readable by others.)

Check it worked:

```bash
ls -l ~/.local/share/spine-backup ~/.config/spine
```

You should see `backup-production.sh` and `restore-check.sh` in the first folder
and `backup.env` in the second.

> **Updating later:** if the scripts in the repository are ever improved, run the
> two `cp` lines again (lines 2 and 3) to install the new versions. Your settings
> file is not touched.

---

## Step 2: take your first backup

No accounts are needed for this. Run the backup by hand:

```bash
~/.local/share/spine-backup/backup-production.sh
```

It takes from a few seconds to a few minutes depending on the size of your data.
You should see something like this (numbers and paths will differ):

```text
2026-09-30 03:26:41 -0400 INFO  === Spine backup starting (run id 20260930T072641Z) ===
2026-09-30 03:26:41 -0400 INFO  config: /Users/you/.config/spine/backup.env
2026-09-30 03:26:41 -0400 SKIP  healthchecks.io: HEALTHCHECK_URL is not set - monitoring pings skipped
2026-09-30 03:26:41 -0400 INFO  backup folder: /Users/you/projects/spine-backups/nightly
2026-09-30 03:26:41 -0400 INFO  disk: 337877 MB free (needed for this run: 1024 MB = the larger of MIN_FREE_MB and twice the last backup)
2026-09-30 03:26:42 -0400 INFO  docker: reachable; 'spine-db' is running and accepting connections
2026-09-30 03:26:42 -0400 INFO  db: dumping 'spine' from container 'spine-db' (pg_dump -Fc)
2026-09-30 03:26:43 -0400 INFO  db: dump written (13.4 MB); verifying
2026-09-30 03:26:43 -0400 INFO  db: verified - 43 tables, all data blocks readable, users_user rows: 5
2026-09-30 03:26:43 -0400 INFO  db: OK /Users/you/projects/spine-backups/nightly/spine-db-20260930T072641Z.dump (13.4 MB, 2s)
2026-09-30 03:26:43 -0400 INFO  media: archiving volume 'spine_media_data' (mounted at /yamtrack/media in 'spine')
2026-09-30 03:26:43 -0400 INFO  media: verified - archive lists 7 entries (176.4 KB)
2026-09-30 03:26:44 -0400 INFO  media: OK /Users/you/projects/spine-backups/nightly/spine-media-20260930T072641Z.tar.gz (176.4 KB, 1s)
2026-09-30 03:26:44 -0400 INFO  monthly: kept this run as the monthly copy for 202609 in /Users/you/projects/spine-backups/monthly
2026-09-30 03:26:44 -0400 SKIP  off-site upload: R2 is not configured (set RCLONE_REMOTE and R2_BUCKET in /Users/you/.config/spine/backup.env to enable it)
2026-09-30 03:26:44 -0400 INFO  prune: database dump - 1 kept, 0 removed (limit 14)
2026-09-30 03:26:44 -0400 INFO  prune: media archive - 1 kept, 0 removed (limit 14)
2026-09-30 03:26:44 -0400 INFO  prune: monthly database dump - 1 kept, 0 removed (limit 3)
2026-09-30 03:26:44 -0400 INFO  prune: monthly media archive - 1 kept, 0 removed (limit 3)
2026-09-30 03:26:44 -0400 INFO  === Backup finished OK (3s) ===
```

The two `SKIP` lines are **normal**: you have not set up R2 or healthchecks.io
yet. What matters is the last line, `Backup finished OK`. Look at the files:

```bash
ls -lh ~/projects/spine-backups/nightly
```

You should see one `spine-db-....dump` and one `spine-media-....tar.gz`.

**If you see `ERROR` lines instead**, the message says what is wrong. The most
common causes are Docker Desktop not running, or containers with different names
(see [Troubleshooting](#troubleshooting)).

---

## Step 3: prove it can be restored

A backup you have never restored is only a hope. `restore-check.sh` starts a
**separate, disposable Postgres container** (the same image as production, with
no network and no access to any production volume), restores the newest dump into
it, counts things, and throws the container away. Production cannot be touched by
construction: the restore happens somewhere else. The only thing it asks the real
database is one read-only user count, so it can show you the two numbers side by
side.

```bash
~/.local/share/spine-backup/restore-check.sh
```

You should see this (numbers will differ), ending in `RESULT: PASS`:

```text
2026-09-30 03:28:11 -0400 INFO  === Spine restore check ===
2026-09-30 03:28:11 -0400 INFO  config: /Users/you/.config/spine/backup.env
2026-09-30 03:28:11 -0400 INFO  dump: /Users/you/projects/spine-backups/nightly/spine-db-20260930T072753Z.dump (13.4 MB, 0h old)
2026-09-30 03:28:11 -0400 PASS  freshness: newest dump is 0h old (limit 36h)
2026-09-30 03:28:12 -0400 INFO  restore: starting a disposable Postgres container 'restore-check-1790000000-4242' from postgres:16-alpine (no network, no production volumes)
2026-09-30 03:28:14 -0400 INFO  restore: free disk space inside Docker: 337877 MB (needed: about 2048 MB)
2026-09-30 03:28:15 -0400 INFO  restore: loading the dump with pg_restore (stops at the first error)
2026-09-30 03:28:17 -0400 PASS  restore: the whole dump loaded without errors (2s)
2026-09-30 03:28:17 -0400 PASS  tables: 43 tables in the restored database
2026-09-30 03:28:17 -0400 PASS  users: 5 user(s) in the restored copy (live database right now: 5)
2026-09-30 03:28:17 -0400 INFO  migrations: 187 applied (latest: app.0042_something)
2026-09-30 03:28:17 -0400 PASS  media: spine-media-20260930T072753Z.tar.gz is readable (7 entries, 176.4 KB, 0h old)
2026-09-30 03:28:18 -0400 INFO  cleanup: disposable container 'restore-check-1790000000-4242' removed
2026-09-30 03:28:18 -0400 INFO  RESULT: PASS - the backup restores cleanly
```

How to read it: every `PASS` is a check that succeeded. The `users` line shows
the user count in the restored copy next to the count in the live database; they
should be close (the backup is from the last run, so it may be a few users
behind). Any `FAIL` line makes the whole script exit with an error, and the
message tells you why. You will run this check every month (see
[Every month](#every-month-prove-the-backups-work)).

The disposable container is held on a short leash, because Docker Desktop keeps
it on the same virtual disk and the same memory as your real database: it may use
at most 2 GB of memory, 2 CPUs and 256 processes (settings `RESTORE_CONTAINER_MEMORY`,
`RESTORE_CONTAINER_CPUS`, `RESTORE_CONTAINER_PIDS`), and the check refuses to
start the restore unless Docker has room for the restored copy (the `free disk
space` line; the larger of 2 GB and five times the dump's size plus 512 MB).

**To stop the check early,** press `Ctrl-C` in its Terminal window: it stops at
once and removes its container. `kill <pid>` from another window is only acted on
when the step that is running has finished (a restore can take minutes), and
`kill -9` stops it instantly but leaves the container to stop and remove itself
after about 70 minutes.

---

## Step 4: create the off-site storage (Cloudflare R2)

Backups on the same Mac as the database do not survive a stolen, flooded or dead
Mac. So a copy goes to Cloudflare R2, a cloud storage service. Its free allowance
is 10 GB, far more than these backups need. Cloudflare may ask you to add a
payment method before it switches R2 on; you are only charged if you go past the
free allowance.

You will do three things in the Cloudflare dashboard: create a **bucket** (a
folder in the cloud), create a **token** that can only touch that bucket, and add
**rules** that delete old copies. Cloudflare occasionally renames buttons; if a
label below is slightly different, pick the closest one.

### 4a. Create the bucket

1. Sign in at <https://dash.cloudflare.com>.
2. In the left menu open **R2 object storage** (you may need to expand *Storage
   & databases* first). If Cloudflare asks you to enable R2 or add a payment
   method, follow the prompts.
3. Click **Create bucket**.
4. **Bucket name:** `spine-backups` (lowercase letters, numbers and hyphens only).
5. Leave the location on **Automatic** and click **Create bucket**.

Leave the bucket **private**. Do not turn on public access or a public URL.

### 4b. Create a token that can only use this bucket

1. Go back to the **R2 object storage** overview page. On the right, in
   *Account Details*, next to **API Tokens** click **Manage**.
2. Click **Create Account API token** (a *User* API token also works).
3. **Token name:** `spine-backup-server`.
4. **Permissions:** choose **Object Read & Write**.
5. Choose to apply the token to **specific buckets only** and select
   `spine-backups`. This is the important part: the token can then do nothing in
   your other buckets and cannot manage your account.
6. Leave the rest at its defaults and click the button that creates the token.

Cloudflare now shows a page with several values. You need exactly three of them:

| Value | What it looks like |
| --- | --- |
| **Access Key ID** | 32 letters and digits |
| **Secret Access Key** | 64 letters and digits, **shown only once** |
| **The S3 endpoint** (Cloudflare calls these "jurisdiction-specific endpoints") | `https://<a long account id>.r2.cloudflarestorage.com` |

Ignore the *Token value* line (it is for a different Cloudflare API). Copy all
three values into your **password manager now**. If you lose the secret, do not
worry: you can delete the token and create a new one. Always copy the endpoint
exactly as Cloudflare shows it, even if it contains `eu` or another jurisdiction.

### 4c. Delete old copies automatically

The backup script never deletes anything in R2, so that a mistake on the server
can never wipe your off-site copies. Instead R2 removes old files itself. You add
two rules. Open the `spine-backups` bucket, go to the **Settings** tab, find
**Object lifecycle rules**, and click **Add rule** twice:

| Rule name | Prefix | Action |
| --- | --- | --- |
| `delete-nightly-after-30-days` | `nightly/` | delete uploaded objects after **30 days** |
| `delete-monthly-after-1-year` | `monthly/` | delete uploaded objects after **365 days** |

Save the changes after each rule. Cloudflare removes expired files within about a
day of their expiry date.

Keep the nightly rule at **14 days or more**: the server keeps its own 14 nights,
and a shorter off-site period would make the script upload files again that R2
just deleted. The prefixes matter: a rule with an *empty* prefix would also delete
your monthly copies after 30 days, which defeats their purpose.

> **Size check for later:** after a few nights, run
> `du -sh ~/projects/spine-backups/nightly`. Off-site nightly usage is roughly
> that size times 30 divided by 14, plus one nightly-sized pair for each monthly
> copy you keep. If that would go over 10 GB, lower the nightly rule to 14 days,
> or lower `KEEP_MEDIA_ARCHIVES` (see [Reference](#reference)).

---

## Step 5: install rclone and connect it to R2

`rclone` is a free command-line tool that copies files to cloud storage. The
backup script uses it to upload to R2.

### 5a. Install it

```bash
brew install rclone
```

Then check it:

```bash
rclone version
```

It should print a version number (rclone 1.59 or newer is needed for R2). If
Terminal says `brew: command not found`, install Homebrew first: it is the
standard installer for Mac command-line tools, and the one-line instructions are
at <https://brew.sh> (it asks for your Mac password). Then run the two commands
above again.

### 5b. Tell rclone about your R2 bucket

Run:

```bash
rclone config
```

rclone asks a series of questions. Type the answers from the right-hand column,
then press Return. Some questions show a numbered list; you can type the **word**
in the table instead of the number, which is safer because the numbers change
between rclone versions.

| rclone asks | Type |
| --- | --- |
| `n) New remote` ... `n/s/q>` | `n` |
| `name>` | `r2` |
| `Storage>` (a long list) | `s3` |
| `provider>` (a list of S3 services) | `Cloudflare` |
| `env_auth>` (*Get AWS credentials from runtime*) | just press Return (the default is `false`) |
| `access_key_id>` | paste your **Access Key ID** |
| `secret_access_key>` | paste your **Secret Access Key** |
| `region>` | just press Return (or type `auto`) |
| `endpoint>` | paste your endpoint, e.g. `https://<account id>.r2.cloudflarestorage.com` |
| `acl>` (only if it asks) | `private` |
| `Edit advanced config?` ... `y/n>` | `n` |
| `Keep this "r2" remote?` ... `y/e/d>` | `y` |
| `e/n/d/r/c/s/q>` | `q` |

If a question appears that is not in the table, press Return to accept the
default.

**rclone shows what you paste, including the secret.** Nothing is hidden on
screen, so make sure nobody is looking over your shoulder, and when you have
finished clear the screen (and the scroll-back) with:

```bash
clear
```

The remote is called `r2`. rclone keeps the credentials in
`~/.config/rclone/rclone.conf` (readable only by you). They are **not** stored in
the backup settings file, and never in the git repository.

### 5c. Test the connection

```bash
rclone lsf r2:spine-backups
```

An empty bucket prints **nothing and no error**, which means it works. (Do not
use `rclone lsd r2:` to test: your token is deliberately not allowed to list all
your buckets, so that command shows `AccessDenied` even though everything is
fine.) If you see an error, see [Troubleshooting](#troubleshooting).

---

## Step 6: set up the alarm (healthchecks.io)

Backups fail silently: nobody notices until the day they are needed.
healthchecks.io fixes that. The script "pings" a secret web address when it
starts, when it succeeds and when it fails. If a ping does not arrive on time,
you get an email.

1. Sign up at <https://healthchecks.io> (the free plan is enough) and confirm
   your email address.
2. Click **Add Check** and fill in:
   * **Name:** `Spine nightly backup`
   * **Schedule:** switch from *Simple* to **Cron**
   * **Cron Expression:** `30 3 * * *` (every day at 3:30)
   * **Time zone** (labelled *Server's Time Zone*): the time zone of the server
     Mac (*System Settings → General → Date & Time* shows it)
   * **Grace Time:** **3 hours**
3. Save. The check shows *New* until the first ping arrives.
4. Copy the check's **ping URL**. It looks like
   `https://hc-ping.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`.

What the two time settings mean:

* The cron expression tells healthchecks.io when a ping is *expected*.
* The **grace time** is how long it waits after that before alerting. Three hours
  is deliberate: if the Mac was asleep at 3:30 and only wakes up (and runs the
  backup) a little later, you are not woken up by a false alarm. It also works as
  a **time limit for one run**: because the script sends a "start" ping,
  healthchecks.io alerts you if a backup starts but does not finish within the
  grace time. (The script itself stops a run that takes longer than 3 hours.)

The success ping carries a one-line summary, for example
`OK run=20260930T073001Z db=13.4MB tables=43 users=5 media=176.4KB entries=7 monthly=created offsite=verified`.
You can read it in the check's log: it tells you at a glance whether the off-site
copy was really verified or only skipped.

Your email address gets the alerts by default. Under *Integrations* you can add
phone notifications (Telegram, Pushover, Slack and others) if you want them
louder than email.

> Treat the ping URL like a password: anyone who has it can report fake
> "everything is fine" messages. It is stored in the settings file, which only
> you can read. The script never prints it, never puts it on a command line, and
> warns you if the settings file is ever readable by other users.

---

## Step 7: tell the backup about R2 and healthchecks.io

Open the settings file in a simple text editor:

```bash
nano ~/.config/spine/backup.env
```

Change these three lines (use your own values, and keep the quotes). Lines that
start with `#` are explanations and are ignored.

```text
RCLONE_REMOTE="r2"
R2_BUCKET="spine-backups"
HEALTHCHECK_URL="https://hc-ping.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

To save in `nano`, press `Ctrl+O`, then Return, then `Ctrl+X` to leave. Do not
put spaces around the `=`. Everything else in the file is optional and explained
inside it.

> Set **both** `RCLONE_REMOTE` and `R2_BUCKET` (or leave both empty). Setting only
> one of them is treated as a mistake, and the backup reports an error.

**Once Step 8 has shown that the upload really works,** add one more line so that
a missing or broken off-site copy can never again pass as a green night:

```text
REQUIRE_OFFSITE=1
```

With it, a run that cannot upload (R2 settings removed, rclone uninstalled, ...)
counts as failed and healthchecks.io emails you. Without it, an unconfigured
off-site copy is only a `SKIP` line in the log.

---

## Step 8: run it by hand and check R2

```bash
~/.local/share/spine-backup/backup-production.sh
```

This time the output has extra lines. Look for these:

```text
... INFO  healthchecks.io: ping '/start' sent
... INFO  off-site: uploading the nightly files from /Users/you/projects/spine-backups/nightly to r2:spine-backups/nightly
... INFO    | spine-db-20260930T073001Z.dump: Copied (new)
... INFO    | spine-media-20260930T073001Z.tar.gz: Copied (new)
... INFO  off-site: verifying the uploaded nightly copies (size and checksum)
... INFO  off-site: uploading the monthly files from /Users/you/projects/spine-backups/monthly to r2:spine-backups/monthly
... INFO  off-site: OK - every local backup file is present in R2 and matches (6s)
... INFO  === Backup finished OK (13s) ===
... INFO  healthchecks.io: ping '/' sent
```

Now confirm it with your own eyes, in three places.

**On the command line:**

```bash
rclone ls r2:spine-backups
```

It lists each file with its size in bytes, for example
`13800000 nightly/spine-db-20260930T073001Z.dump`.

**In the Cloudflare dashboard:** **R2 object storage** → `spine-backups` →
**Objects** tab → the `nightly` and `monthly` folders show the same files.

**In healthchecks.io:** the *Spine nightly backup* check should now be green
(*Up*), and its log shows a "started" and a "success" entry with the duration and
the summary line.

If the first upload is slow, that is normal: it uploads every file in the local
folder. From then on only new files are uploaded.

**Finally, prove that the copy in R2 can be downloaded and restored.** This is the
copy you would use if the Mac were gone, so test it once for real now:

```bash
~/.local/share/spine-backup/restore-check.sh --offsite
```

It downloads the newest dump and media archive from R2 and runs the same checks on
them; you want to see `RESULT: PASS`. (Why now: rclone's documentation and issue
tracker describe Cloudflare sometimes serving `.gz` files in a different form from
the one that was uploaded, which shows up as `corrupted on transfer: sizes
differ`. The download test would catch that straight away. If it ever happens, look
up rclone's `--s3-might-gzip` option.)

---

## Step 9: make it run every night

macOS starts scheduled jobs with a system called **launchd**. A small settings
file (a "LaunchAgent") tells it to run the backup at 3:30 am and to write the
output to `~/Library/Logs/spine-backup.log`.

> **Do this from the Mac's own desktop session** (sit at the Mac, or use Screen
> Sharing) in a Terminal window of the user that is logged in and runs Docker
> Desktop. The `launchctl bootstrap gui/...` command below needs a graphical login
> session; over plain SSH it can fail with "Domain does not support specified
> action" or "Bootstrap failed: 5".

### Install and load it

First make sure the folders exist:

```bash
mkdir -p ~/Library/LaunchAgents ~/Library/Logs
```

Install the LaunchAgent. The `sed` part replaces the placeholder `__HOME__` in the
template with your real home folder (launchd cannot expand `~` by itself):

```bash
sed "s#__HOME__#$HOME#g" ~/projects/spine/scripts/launchd/com.spine.backup.plist > ~/Library/LaunchAgents/com.spine.backup.plist
```

Check that the file is valid. It should print `...: OK`:

```bash
plutil -lint ~/Library/LaunchAgents/com.spine.backup.plist
```

Load it. From now on launchd runs it every night:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.spine.backup.plist
```

macOS may show a notification that a background item was added. That is
expected. If backups later do not run, look in *System Settings → General → Login
Items & Extensions* and make sure the item is allowed to run in the background.

Confirm it is loaded. You should see one line ending in `com.spine.backup`:

```bash
launchctl list | grep com.spine.backup
```

### Test it now (do not wait until 3:30 am)

This tells launchd to run the job immediately, exactly the way it will at night:

```bash
launchctl kickstart gui/$(id -u)/com.spine.backup
```

Wait about a minute, then look at the log:

```bash
tail -n 25 ~/Library/Logs/spine-backup.log
```

You should see the same lines as in Step 8, ending in `Backup finished OK`. Run
`launchctl list | grep com.spine.backup` again: the number in the middle is the
exit status of the last run, and `0` means success.

### Turning it off, and on again

To unload it (stops the nightly runs; a backup that is running at that moment is
stopped cleanly within a couple of seconds):

```bash
launchctl bootout gui/$(id -u)/com.spine.backup
```

To load it again:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.spine.backup.plist
```

To remove it completely, unload it as above and then delete the file:

```bash
rm ~/Library/LaunchAgents/com.spine.backup.plist
```

**To change the time**, edit the `Hour` and `Minute` numbers in
`~/Library/LaunchAgents/com.spine.backup.plist` (for example with `nano`), unload
it, load it again, and change the healthchecks.io cron expression to match.
launchd does not notice the edit until you unload and load.

**The log file** grows by a few lines a night. If you ever want to empty it:

```bash
: > ~/Library/Logs/spine-backup.log
```

You are done. From tomorrow the backups run by themselves. Put a monthly
reminder in your calendar for the next section.

---

## Every month: prove the backups work

Once a month, on the server Mac:

```bash
~/.local/share/spine-backup/restore-check.sh
```

It should end with `RESULT: PASS` (see Step 3 for what the output looks like).

Every few months, also prove that the copy **in R2** is good. It is the copy you
would use if the Mac were gone:

```bash
~/.local/share/spine-backup/restore-check.sh --offsite
```

That downloads the newest dump and media archive from R2 and runs the same
checks on them.

To test one particular file, for example a dump taken before a risky
deployment, give its name (deployment dumps only exist once the deploy workflow
that makes them has been merged, see the table at the top):

```bash
~/.local/share/spine-backup/restore-check.sh ~/projects/spine-backups/pre-deploy-123456789-1.dump
```

**If it says `FAIL`:** read the line that starts with `FAIL`. It tells you the
reason (for example "newest dump is 52h old: the nightly backup may have stopped
running"). Fix that, run `~/.local/share/spine-backup/backup-production.sh` to
make a fresh backup, and run the check again. Do not consider your data safe
until you see `PASS`.

The check needs free disk space inside Docker for the restored copy (several
times the size of the dump; it checks first and refuses to start if there is not
enough) for a few seconds to a few minutes, and it cleans up after itself. If it
is ever interrupted so badly that it cannot (a power cut, `kill -9`), the
disposable container stops and removes itself after about 70 minutes, even if a
restore is still running in it.

---

## Disaster recovery: getting everything back

Use this when the database is lost or damaged, when someone deleted the wrong
data, or when you are setting up a replacement Mac. **Read the whole section
first, then follow it in order.** Nothing here needs you to be quick; a few
minutes of care matter more than speed.

**The safety idea:** the current database is never deleted. The backup is first
restored into a **new, separate database** next to it and checked. Only when it
looks right is it **swapped in by renaming**, and the old data stays where it is
(renamed `spine_before_restore_<time>`) until you choose to delete it. Every
command that changes something is one single chain that stops at the first problem
and refuses to start unless its checks pass, so even a half-pasted command cannot
hurt.

**What you need:**

* Terminal on the server Mac, and Docker Desktop running.
* A backup: either the files on the Mac (`~/projects/spine-backups/nightly`) or
  the copy in R2 (needs rclone, Step 5, and your R2 keys from the password
  manager).
* On a **new Mac only:** your `.env.production` (from your password manager).
  Without it you must create new secrets (see
  [What is NOT backed up](#what-is-not-backed-up-read-this)); the data itself can
  still be restored.
* Free disk space inside Docker for a second copy of the database until you delete
  the old one.

**Make sure nothing else is using the database while you work.** The nightly
backup and every deployment use the same `spine-db` container, and this recovery
renames databases in it:

* **Do not start a recovery around 3:30 am**, or while a backup is running (it can
  take several minutes). To check, type `pgrep -fl backup-production.sh`: it
  prints nothing when no backup is running. The safest way is to switch the
  nightly job off while you work (the `launchctl bootout` command under
  [Turning it off, and on again](#turning-it-off-and-on-again)); Part J switches it
  on again.
* **Do not start one while a deployment is running.** A deployment restarts the
  app and applies database migrations. Look at the GitHub Actions page of the
  repository and wait until no deploy is in progress.

In the commands below, `spine`, `spine-db` and the media mount `/yamtrack/media`
are the production names from `docker-compose.production.yml`.

> **Use one Terminal window from Part B to Part H.** The commands rely on shell
> variables (`RESTORE_DIR`, `DB_DUMP`, `MEDIA_ARCHIVE`, `NEWDB`, `OLDDB`, `TS`,
> `CREATED`, `RESTORED_OK`) that disappear when you close the window. If you do
> close it, start again at Part B: nothing has been lost, and the commands
> refuse to run without their variables. (Going back after the swap is the one
> exception: [that section](#if-the-restored-data-turns-out-to-be-wrong-go-back)
> shows how to do it from a fresh window.)

### Part A. New Mac only: get the stack ready (skip this on the same Mac)

Install Docker Desktop from <https://www.docker.com/products/docker-desktop/>,
start it, and wait until it says it is running. Then get the code (if the
repository is private, GitHub asks you to sign in):

```bash
mkdir -p ~/projects && cd ~/projects && git clone https://github.com/armaandave/spine.git spine
```

Switch to the branch you normally deploy (for example `cd ~/projects/spine &&
git checkout myapp-main`). Put your saved `.env.production` file into
`~/projects/spine` and lock it down:

```bash
chmod 600 ~/projects/spine/.env.production
```

Start **only** the database and redis, and wait until Docker's own health checks
say they are ready. (`--wait` relies on the `healthcheck:` entries for `db` and
`redis` that `docker-compose.production.yml` gets in the same change as these
scripts: with an older compose file that has none, `--wait` returns as soon as
the containers have started, and the `pg_isready` check below is what tells you
when the database is really ready, so repeat it until it says `accepting
connections`.) Do not start the app yet: it would fill the database with empty
tables before the backup goes in.

```bash
cd ~/projects/spine
docker compose --project-name spine --env-file .env.production -f docker-compose.production.yml up -d --wait db redis
```

Then ask Postgres directly, over its network port. (The `-h 127.0.0.1` matters: while
the Postgres image sets up a new data folder it runs a temporary server that only
answers on a local socket, so a check without it can say "ready" too early.) It
must print `accepting connections`:

```bash
docker exec spine-db pg_isready -h 127.0.0.1 -U spine -d spine
```

### Part B. Get the backup files into one folder

**Option 1: the backups are still on the Mac.**

```bash
export RESTORE_DIR=~/projects/spine-backups/nightly
```

**Option 2: download them from R2** (needs rclone set up as in Step 5). First
choose where to put them and look at what is in the bucket (the newest names are
at the bottom):

```bash
export RESTORE_DIR=~/projects/spine-backups/restore
mkdir -p "$RESTORE_DIR"
rclone lsf r2:spine-backups/nightly | sort
```

Then find the newest database dump and media archive and download them:

```bash
DB_DUMP="$(rclone lsf r2:spine-backups/nightly | grep -E '^spine-db-[0-9]{8}T[0-9]{6}Z\.dump$' | sort | tail -n 1)"
MEDIA_ARCHIVE="$(rclone lsf r2:spine-backups/nightly | grep -E '^spine-media-[0-9]{8}T[0-9]{6}Z\.tar\.gz$' | sort | tail -n 1)"
echo "$DB_DUMP and $MEDIA_ARCHIVE"
rclone copyto "r2:spine-backups/nightly/$DB_DUMP" "$RESTORE_DIR/$DB_DUMP"
rclone copyto "r2:spine-backups/nightly/$MEDIA_ARCHIVE" "$RESTORE_DIR/$MEDIA_ARCHIVE"
```

**For both options,** go to the folder and pick the files. The pattern only
matches genuine backups and ignores anything else in the folder:

```bash
cd "$RESTORE_DIR"
DB_DUMP="$(ls -1 | grep -E '^spine-db-[0-9]{8}T[0-9]{6}Z\.dump$' | sort | tail -n 1)"
MEDIA_ARCHIVE="$(ls -1 | grep -E '^spine-media-[0-9]{8}T[0-9]{6}Z\.tar\.gz$' | sort | tail -n 1)"
ls -lh "$DB_DUMP" "$MEDIA_ARCHIVE"
```

> **Want an older backup instead of the newest** (for example because the bad
> data was already in last night's backup)? Set the names yourself, for example
> `DB_DUMP=spine-db-20260928T073001Z.dump`, and use the media archive from the
> same day. `ls` shows what is available. Monthly copies live in
> `~/projects/spine-backups/monthly`; to use one, set
> `RESTORE_DIR=~/projects/spine-backups/monthly` and run the second box above.

### Part C. Stop the app (same Mac only)

So that nothing writes to the database while it is being replaced. On a new Mac
there is no app container yet, and "No such container" is fine:

```bash
docker stop spine
```

### Part D. Check the dump BEFORE you change anything

This reads the whole dump and proves it is complete. Nothing has been changed
yet. **If it prints `STOP`, do not continue:** pick another backup (go back to
Part B).

```bash
[ -f "$DB_DUMP" ] && [ -s "$DB_DUMP" ] && docker exec -i spine-db pg_restore -f /dev/null < "$DB_DUMP" && echo "OK: the dump is readable from start to finish" || echo "STOP: this dump cannot be used, do not continue"
```

### Part E. Restore the backup into a NEW database

This creates a second database next to the real one and loads the backup into it.
The real database is not touched. First choose the names (this line only sets
variables and is safe on its own; the names use only digits and underscores on
purpose, because Postgres silently turns capital letters in database names into
small ones):

```bash
TS=$(date -u +%Y%m%d_%H%M%S); NEWDB=spine_restore_$TS; OLDDB=spine_before_restore_$TS; RESTORED_OK=; CREATED=; echo "new copy: $NEWDB   the old data will be kept as: $OLDDB"
```

Now the restore itself. It is one chain: it checks the dump again, creates the new
database, loads the dump (stopping at the first error), and only prints `OK` if the
restored copy really contains users. It then prints the live database's numbers
next to the restored ones, so you can compare them. If any link fails it prints
`STOP`, and a new database that had already been created is dropped again, so no
half-restored copy is left behind (only that new one: the real `spine` database is
never touched):

```bash
CREATED=
[ -n "$NEWDB" ] && [ -f "$DB_DUMP" ] \
  && docker exec -i spine-db pg_restore -f /dev/null < "$DB_DUMP" \
  && docker exec spine-db psql -U spine -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $NEWDB OWNER spine" \
  && CREATED=$NEWDB \
  && docker exec -i spine-db pg_restore -U spine -d "$NEWDB" --no-owner --no-acl --exit-on-error < "$DB_DUMP" \
  && [ "$(docker exec spine-db psql -U spine -d "$NEWDB" -Atc 'select count(*) from users_user')" -gt 0 ] \
  && docker exec spine-db psql -U spine -d "$NEWDB" -Atc "select 'OK: the restored copy has ' || (select count(*) from users_user) || ' users and ' || (select count(*) from information_schema.tables where table_schema = 'public') || ' tables'" \
  && { docker exec spine-db psql -U spine -d "$NEWDB" -Atc "select 'Its latest migration: ' || app || '.' || name from django_migrations order by id desc limit 1" 2>/dev/null || true; } \
  && { docker exec spine-db psql -U spine -d spine -Atc "select 'For comparison, the live database has ' || (select count(*) from users_user) || ' users; its latest migration: ' || (select app || '.' || name from django_migrations order by id desc limit 1)" 2>/dev/null || echo "(No numbers could be read from a live database called spine to compare with: normal on a new Mac.)"; } \
  && RESTORED_OK=$NEWDB \
  || { echo "STOP: the restore did not finish. Your real database was not touched."; [ -n "$CREATED" ] && [ "$CREATED" = "$NEWDB" ] && docker exec spine-db psql -U spine -d postgres -c "DROP DATABASE \"$NEWDB\" WITH (FORCE)" && echo "The half-restored copy $NEWDB was removed again."; }
```

Look at the numbers it printed. Do the user count and the latest migration look
right next to the live database's (roughly the number of users you expect, and a
migration that is the same as, or a little older than, the live one)? If they do
not, stop here: nothing has been swapped, and the unwanted copy can be removed
with the command in Part J. If it printed `STOP`, read the message above it, fix
the problem (or choose another backup in Part B) and run the two boxes of this
part again.

*Why `--no-owner --no-acl`:* they make every restored table belong to the `spine`
role that runs the restore, and skip the dump's grants to other roles. That is
what you want on a new Mac, where the database may not have the same roles as
the old one did, and it stops a role that does not exist there from aborting
the restore. If you ever created extra roles by hand (for example a read-only
role for reports), create them and grant their access again afterwards.

### Part F. Swap the restored copy in (nothing is deleted)

This is the only step that changes which data the app will use. It renames the
current database out of the way and renames the restored copy to `spine`, in one
step. It refuses to run unless **all** of these are true: the restore in Part E
succeeded in this window, Docker answers and the app is **not running** (it asks
Docker for the app's state, and anything but "stopped" or "not there at all"
stops the chain, including Docker not answering), the restored copy exists, and it
still has users. The old data is kept under the name in `$OLDDB`.

```bash
[ -n "$RESTORED_OK" ] && [ "$RESTORED_OK" = "$NEWDB" ] && [ -n "$OLDDB" ] \
  && APP_STATE=$(docker ps -a --filter 'name=^spine$' --format '{{.State}}') \
  && { [ -z "$APP_STATE" ] || [ "$APP_STATE" = exited ] || [ "$APP_STATE" = created ] || [ "$APP_STATE" = dead ]; } \
  && [ "$(docker exec spine-db psql -U spine -d postgres -Atc "select count(*) from pg_database where datname = '$NEWDB'")" = 1 ] \
  && [ "$(docker exec spine-db psql -U spine -d "$NEWDB" -Atc 'select count(*) from users_user')" -gt 0 ] \
  && docker exec spine-db psql -U spine -d postgres -Atq -v ON_ERROR_STOP=1 -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'spine'" \
  && sleep 2 \
  && docker exec spine-db psql -U spine -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE spine RENAME TO $OLDDB; ALTER DATABASE $NEWDB RENAME TO spine;" \
  && echo "SWAP OK: the restored data is now 'spine'. The old data is kept as $OLDDB" \
  || echo "STOP: nothing was swapped."
```

If it printed `SWAP OK`, carry on. If it printed `STOP`, nothing changed: check
that the app is stopped (Part C) and that Part E printed `OK`, then try again. If
the message above `STOP` says `database "spine" does not exist` (the old database
is already gone), use this variant instead, which only renames the restored copy:

```bash
[ -n "$RESTORED_OK" ] && [ "$RESTORED_OK" = "$NEWDB" ] \
  && APP_STATE=$(docker ps -a --filter 'name=^spine$' --format '{{.State}}') \
  && { [ -z "$APP_STATE" ] || [ "$APP_STATE" = exited ] || [ "$APP_STATE" = created ] || [ "$APP_STATE" = dead ]; } \
  && [ -z "$(docker exec spine-db psql -U spine -d postgres -Atc "select 1 from pg_database where datname = 'spine'")" ] \
  && docker exec spine-db psql -U spine -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE $NEWDB RENAME TO spine" \
  && echo "OK: the restored data is now 'spine'" \
  || echo "STOP: nothing was changed."
```

### Part G. Start the app

**Same Mac:**

```bash
docker start spine
```

**New Mac** (this builds the app image, which takes a few minutes, and creates
the media volume):

```bash
cd ~/projects/spine
docker compose --project-name spine --env-file .env.production -f docker-compose.production.yml up -d --build --wait
```

The app applies any missing database migrations by itself when it starts.

### Part H. Restore the media files

Find the media volume by asking the app container (never guess its name):

```bash
MEDIA_VOLUME="$(docker inspect spine --format '{{range .Mounts}}{{if eq .Destination "/yamtrack/media"}}{{.Name}}{{end}}{{end}}')"
echo "media volume: $MEDIA_VOLUME"
```

If that printed an empty name, the app container does not exist yet: go back to
Part G. Otherwise unpack the archive into the volume (the `postgres:16-alpine`
image is already on the Mac and includes `tar`). The unusual folder names are
deliberate: Docker copies an image's own files into a still-empty volume when it is
first mounted, so the volume must not be mounted on a folder that exists in the
image:

```bash
docker run --rm --pull=never --network none --entrypoint tar -v "$MEDIA_VOLUME":/restore-target -v "$RESTORE_DIR":/restore-source:ro postgres:16-alpine -xzf "/restore-source/$MEDIA_ARCHIVE" -C /restore-target
```

Files with the same name are overwritten; nothing else in the volume is deleted.

### Part I. Check that everything works

The app answers locally:

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health/
```

The app answers through Cloudflare (on a new Mac this only works once the
Cloudflare tunnel has been set up again):

```bash
curl -fsS https://api.spine-api.com/api/v1/health/
```

The database structure is up to date:

```bash
docker exec spine python manage.py migrate --check
```

Then open the app, log in, and look at your diary and a few profile pictures.

*Optional, and usually not needed:* the next command empties the app's cache. In
this setup the cache and the queue of background jobs share one Redis database,
so it **also throws away every background job that is waiting** (for example an
import a user just started, which would have to be started again). Only run it
if you see stale data after the restore:

```bash
docker exec spine python manage.py shell -c 'from django.core.cache import cache; cache.clear()'
```

### Part J. Afterwards

* Run a backup by hand to make sure the restored setup is protected:
  `~/.local/share/spine-backup/backup-production.sh`
* **If you switched the nightly backup off** before you started (see the warning at
  the top of this section), switch it on again:
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.spine.backup.plist`
* **On a new Mac,** repeat Steps 1, 5, 7 and 9 of this page (install the
  scripts, rclone, the settings file and the LaunchAgent), and set the GitHub
  deploy runner up again so deployments work as before.
* **Once you are sure the restored data is right (days later is fine), free the
  space** taken by the old database. First list what there is, with sizes:

  ```bash
  docker exec spine-db psql -U spine -d postgres -Atc "select datname, pg_size_pretty(pg_database_size(datname)) from pg_database where datname like 'spine\_before\_restore\_%' or datname like 'spine\_restore\_%' or datname like 'spine\_rejected\_%' order by 1"
  ```

  Then delete one by its exact name, copied from that list. In the command below,
  replace the **whole** name between the two double quotes (everything from
  `spine_before_restore_` up to and including `HERE`) with the name you copied, and
  keep the quotes. As printed, the command refuses to do anything, because no
  database has that placeholder name:

  ```bash
  docker exec spine-db psql -U spine -d postgres -c 'DROP DATABASE "spine_before_restore_PASTE_THE_EXACT_NAME_HERE"'
  ```

  A `spine_restore_...` entry in the list is a restored copy that was never
  swapped in (for example from a restore you abandoned after Part E printed its
  numbers). A `spine_rejected_...` entry is the restored copy that you went back
  from. Delete both the same way.
* Delete the `restore` folder when you no longer need it.

### If the restored data turns out to be wrong: go back

While the old database still exists you can swap back in seconds. Stop the app
first:

```bash
docker stop spine
```

**Still in the window you used for Part E?** Then `$OLDDB` and `$TS` are still
set: skip to the last box.

**Closed the window, or not sure?** The variables are gone, so set them by hand.
List the old databases with the first command in
[Part J](#part-j-afterwards) and copy the exact name of the
`spine_before_restore_...` entry to go back to. Put it into the box below in place
of the **whole** placeholder name (from `spine_before_restore_` up to and
including `HERE`). The rest of the line works out the time part by itself. As
printed, the swap-back box that follows refuses to do anything:

```bash
OLDDB=spine_before_restore_PASTE_THE_EXACT_NAME_HERE; TS=${OLDDB#spine_before_restore_}; echo "going back to: $OLDDB"
```

Then the swap back (it needs `$OLDDB` and `$TS`, and, like Part F, it refuses to
run unless Docker answers and the app is not running):

```bash
[ -n "$OLDDB" ] && [ -n "$TS" ] \
  && APP_STATE=$(docker ps -a --filter 'name=^spine$' --format '{{.State}}') \
  && { [ -z "$APP_STATE" ] || [ "$APP_STATE" = exited ] || [ "$APP_STATE" = created ] || [ "$APP_STATE" = dead ]; } \
  && [ "$(docker exec spine-db psql -U spine -d postgres -Atc "select count(*) from pg_database where datname = '$OLDDB'")" = 1 ] \
  && docker exec spine-db psql -U spine -d postgres -Atq -v ON_ERROR_STOP=1 -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'spine'" \
  && sleep 2 \
  && docker exec spine-db psql -U spine -d postgres -v ON_ERROR_STOP=1 -c "ALTER DATABASE spine RENAME TO spine_rejected_$TS; ALTER DATABASE $OLDDB RENAME TO spine;" \
  && echo "ROLLED BACK: the old data is 'spine' again; the rejected copy is kept as spine_rejected_$TS" \
  || echo "STOP: nothing was changed."
```

Then `docker start spine`.

### If only some of it is lost

* **Only the media files** (profile pictures missing): do Parts B, G (start the app
  if it is stopped) and H only.
* **Only the database:** do Parts B to G (with `docker start spine`).

---

## Troubleshooting

| What you see | What it means and what to do |
| --- | --- |
| `the Docker daemon is not reachable - is Docker Desktop running?` | Docker Desktop is not running (perhaps after a restart). Start it, and turn on *Start Docker Desktop when you sign in* (see [Before you start](#before-you-start), item b). The script waits up to 60 seconds for Docker to wake up. |
| `docker did not answer within 30s` or `... timed out after ...s` | Docker Desktop is stuck: a Docker call hung and was stopped by the time limit. Restart Docker Desktop. The next night's run is unaffected. **If Docker is fine but your database has simply grown** (the message says `timed out after 3600s` for the dump, the verification or the upload, and it happens every night), the step needs more than an hour: raise `STEP_TIMEOUT_SECONDS` in `backup.env`, and `MAX_RUN_SECONDS` with it, because the whole run must be allowed to last longer than its longest step. |
| `db: pg_dump gave up after waiting 120s for a table lock` | Something (a database migration, a long-running job, someone in `psql`) holds a lock on a table that the dump needs. The dump did not wait for as long as the lock lasts, and nothing was left running. Run the backup again when the job has finished; if it keeps happening at 3:30 am, find the job that runs then. `DUMP_LOCK_WAIT_SECONDS` sets how long it waits. |
| `database container 'spine-db' does not exist` | The container name differs from the default. Check `docker ps -a`, then set `DB_CONTAINER` in `~/.config/spine/backup.env`. |
| `database container ... exists but is not running` / `does not accept connections` | The stack is stopped or still starting. Start it, then run the backup again. |
| `media: app container 'spine' was not found` | The app container is missing (perhaps a deployment was running at that moment). The database dump is not affected and was still taken; the run is reported as failed so you know the media archive is missing. Set `APP_CONTAINER` if the name differs. |
| `the docker CLI was not found` | Docker Desktop's command-line tools are in an unusual place. Find them with `which docker`, then set `EXTRA_PATH="/that/folder"` in the settings file. |
| `rclone is not installed` | Run `brew install rclone`. If it is installed somewhere unusual, set `EXTRA_PATH`. |
| `R2 is only half configured` | Set both `RCLONE_REMOTE` and `R2_BUCKET`, or clear both. |
| `off-site upload is REQUIRED` | `REQUIRE_OFFSITE=1` is set but R2 is not configured. Configure it (Steps 4 to 7) or remove that line. |
| `off-site: ... FAILED` with `AccessDenied` or `403` | The token does not allow this bucket. Check that the token is **Object Read & Write**, limited to `spine-backups`, and that the bucket name in `backup.env` is exact. (The script already tells rclone not to try to create buckets, which a bucket-limited token is not allowed to do.) |
| `off-site: ... FAILED` with `didn't find section in config file` | The remote name in `backup.env` does not match. `rclone listremotes` shows the real name (with a colon; leave the colon out). |
| `off-site: ... FAILED` with `no such host` or a timeout | The Mac has no internet, or the endpoint is wrong. The next night's run uploads whatever was missed, so nothing is lost. |
| rclone says `corrupted on transfer: sizes differ` for a `.tar.gz` | Cloudflare served the gzip file in a different form from the one that was uploaded. Look up rclone's `--s3-might-gzip` option in its S3 documentation. |
| `another backup is already running (pid ...)` | A backup really is running (yours, or the 3:30 one). The second run does nothing, tells healthchecks.io nothing, and exits with status 75. A run that hangs is stopped by itself after 3 hours. The lock is held by the operating system for as long as any part of the run is alive, so a crashed or killed run never leaves one behind to clear by hand. If the message says the pid `is gone`, the nightly job was killed outright and what it had started is stopping by itself: wait a few seconds and run it again. |
| `... exists but is not a plain lock file` or `cannot open the lock file` / `could not create the lock` | This is a real failure, not "somebody else is running" (healthchecks.io is told, exit status 1). Either something that is not an ordinary file sits at the lock path the message names (for example a leftover folder: look at it with `ls -la`, and remove it by hand once you have seen what it is), or the backup folder is not writable (check with `ls -ld ~/projects/spine-backups`). |
| `sanity: the dump holds only 0 user(s)` / `more than 50% smaller` / `holds only 1 entry` | The new files were kept (they are valid) but the run is reported as failed. Find out why: is this the right database, did something delete data, is the media volume really empty? If the situation is expected, adjust `MIN_USERS` or `ALLOW_EMPTY_MEDIA` in `backup.env`. **If you deleted data on purpose** (the database really is smaller now), run ONE backup with `ACCEPT_SMALLER_DB=1 ~/.local/share/spine-backup/backup-production.sh` on the command line: it skips the size check, and its dump becomes the new yardstick from then on. Without that, every following night still compares with the biggest healthy dump of the last week (a flagged dump never counts as the yardstick), so the alarm keeps going until you say so. (`ACCEPT_SMALLER_DB` is ignored in `backup.env`, so it cannot be left on by accident.) |
| `size check skipped - every recent dump was flagged` | The run could not be size-checked because every earlier dump in the window had been flagged, so no monthly copy is made from it either. Look at why the earlier nights were flagged; once you are satisfied that the current database is right, run one backup with `ACCEPT_SMALLER_DB=1` as in the row above. |
| `config: ... must be ... without leading zeros` | A number such as `KEEP_MONTHLY=08` was given: the shell would read it as a broken octal number. Write `8`. |
| `config: NIGHTLY_SUBDIR and MONTHLY_SUBDIR must be different folders` or `... must be a plain folder name` | The two sub-folders must be two different plain names (no `/`, not `.` or `..`, not starting with a dot); on a Mac `Nightly` and `nightly` count as the same folder. |
| `prune: the system clock looks wrong` | The Mac's date is earlier than that of a backup that already exists, so "oldest" cannot be trusted and nothing was deleted. Fix the date (*System Settings → General → Date & Time*, set automatically). The message names the file that looks too new: if the clock was once wrongly set into the *future* and that file is bogus, delete it (and its partner with the same time stamp) and the next run prunes normally again. |
| `Backup INTERRUPTED by signal TERM` (or `the time limit`) | The job was stopped: the Mac was shutting down, you ran `launchctl bootout`, or the 3-hour limit was reached. Temporary files and the lock were cleaned up. |
| `config file ... is readable by other users` | Run `chmod 600 ~/.config/spine/backup.env`. |
| `healthchecks.io answered 'OK (not found)' instead of 'OK'` | The ping URL in `backup.env` is mistyped. Copy it again from the check's page. |
| `not enough free disk space` | Free some space, or lower `KEEP_DB_DUMPS` / `KEEP_MEDIA_ARCHIVES`. The run needs the larger of `MIN_FREE_MB` and twice the size of the last backup. |
| `db: verification FAILED` | The fresh dump was damaged, so it was thrown away and the earlier good backups are untouched. Run the backup again. If it keeps failing, look at the lines after the message and at the disk space. |
| healthchecks.io says **down** but the log shows success | The ping URL in `backup.env` may be wrong, or the Mac had no internet at that moment (the log then shows `WARN healthchecks.io: ping ... could not be delivered`). Check the URL, the cron time zone and the grace time. |
| healthchecks.io says **down** and the log has nothing for that night | The job did not run: the Mac was off or asleep the whole time, or the LaunchAgent is not loaded (`launchctl list \| grep com.spine.backup`). |
| `restore-check.sh` says `disposable container(s) from an interrupted earlier check` | An earlier check was killed before it could clean up. It removes itself after about 70 minutes; to remove it now run the command it prints (`docker rm -f -v <name>`). |
| `restore: could not start the disposable container` or `the image ... is not on this machine` | The Postgres image is not installed on this machine (the check never downloads anything). Start the production stack once, or set `RESTORE_CHECK_IMAGE` to an installed Postgres image. |
| `restore: not enough free disk space inside Docker` | Docker Desktop's virtual disk is nearly full (production shares it), so the check refused to restore. `docker system df` shows what uses it; free some space in Docker Desktop (*Settings → Resources*, or remove images you no longer need) and run the check again. Nothing was restored. |
| The log shows `Backup FAILED before anything was written` | One of the checks at the start failed. The `ERROR` line just above says which. |

**Where to look first:** the last lines of the log,
`tail -n 40 ~/Library/Logs/spine-backup.log`, and the list of files,
`ls -lht ~/projects/spine-backups/nightly | head`.

---

## Reference

### Cheat sheet

| I want to... | Type |
| --- | --- |
| take a backup right now | `~/.local/share/spine-backup/backup-production.sh` |
| see what happened last night | `tail -n 40 ~/Library/Logs/spine-backup.log` |
| list the backups | `ls -lht ~/projects/spine-backups/nightly \| head` |
| prove the backups work | `~/.local/share/spine-backup/restore-check.sh` |
| prove the R2 copy works | `~/.local/share/spine-backup/restore-check.sh --offsite` |
| see what is in R2 | `rclone ls r2:spine-backups` |
| run the nightly job now, the way launchd does | `launchctl kickstart gui/$(id -u)/com.spine.backup` |

### Files and folders

| Path | What it is |
| --- | --- |
| `~/projects/spine-backups/nightly/` | nightly database dumps and media archives |
| `~/projects/spine-backups/monthly/` | the monthly copies (hard links of the first good nightly files of each month) |
| `~/projects/spine-backups/.backup.lock` | the lock file (a line saying which run holds it). It stays in place between runs and is never deleted: the lock is held by the operating system, not by the file's existence |
| `~/projects/spine-backups/pre-deploy-*.dump` | dumps made before each deployment, once the deploy workflow that makes them is merged (not pruned by these scripts) |
| `~/.local/share/spine-backup/backup-production.sh` | the nightly backup (installed copy) |
| `~/.local/share/spine-backup/restore-check.sh` | the monthly restore check (installed copy) |
| `~/.config/spine/backup.env` | your backup settings (never in the repository) |
| `~/.config/rclone/rclone.conf` | rclone's remotes and R2 credentials (made by `rclone config`) |
| `~/Library/LaunchAgents/com.spine.backup.plist` | the nightly schedule |
| `~/Library/Logs/spine-backup.log` | what the nightly runs printed |
| `scripts/backup-production.sh`, `scripts/restore-check.sh`, `scripts/backup.env.example`, `scripts/launchd/com.spine.backup.plist` | the originals in the repository |

### Settings (`~/.config/spine/backup.env`)

All optional; the defaults suit the production compose file. Anything already set
in the environment overrides the file (for example
`KEEP_DB_DUMPS=30 ~/.local/share/spine-backup/backup-production.sh`). Point the
scripts at another file with `SPINE_BACKUP_CONFIG=/path/to/file`. Numbers are
checked: a value that is not a short whole number without leading zeros (for
example a huge one, or `08`) makes the run stop with an error instead of being
used. One more setting is **not** read from this file on purpose:
`ACCEPT_SMALLER_DB=1`, for a single run after you deleted data deliberately (see
[Troubleshooting](#troubleshooting)).

| Setting | Default | Meaning |
| --- | --- | --- |
| `RCLONE_REMOTE` | empty | name of the rclone remote (`r2`). Empty: skip the upload |
| `R2_BUCKET` | empty | R2 bucket name. Empty: skip the upload |
| `R2_PREFIX` | `nightly` | folder inside the bucket for the nightly files |
| `R2_MONTHLY_PREFIX` | `monthly` | folder inside the bucket for the monthly copies |
| `REQUIRE_OFFSITE` | `0` | `1`: a run without a verified off-site copy counts as failed |
| `HEALTHCHECK_URL` | empty | healthchecks.io ping URL. Empty: no pings |
| `BACKUP_ROOT` | `~/projects/spine-backups` | where backups are kept |
| `NIGHTLY_SUBDIR` / `MONTHLY_SUBDIR` | `nightly` / `monthly` | sub-folders for the two kinds of copy |
| `KEEP_DB_DUMPS` | `14` | newest nightly dumps to keep (1 to 9999) |
| `KEEP_MEDIA_ARCHIVES` | `14` | newest nightly media archives to keep (1 to 9999) |
| `KEEP_MONTHLY` | `3` | monthly copies to keep locally (0 to 999; 0 turns them off) |
| `MIN_USERS` | `1` | a dump with fewer rows in `users_user` fails the run (0 turns the check off) |
| `MAX_SIZE_DROP_PERCENT` | `50` | a dump this many percent smaller than the biggest of the last `SIZE_BASELINE_DUMPS` healthy dumps (and the newest monthly one) fails the run (0 turns it off) |
| `SIZE_BASELINE_DUMPS` | `7` | how many of the newest healthy nightly dumps the size check looks at (1 to 999) |
| `ALLOW_EMPTY_MEDIA` | `0` | `1`: an (almost) empty media archive is acceptable |
| `DB_CONTAINER` | `spine-db` | Postgres container |
| `APP_CONTAINER` | `spine` | app container (used to find the media volume) |
| `DB_NAME` / `DB_USER` | `spine` / `spine` | database and role |
| `MEDIA_MOUNT_PATH` | `/yamtrack/media` | where the app container mounts the media volume |
| `MEDIA_HELPER_IMAGE` | image of the DB container | image for the throwaway archiving container |
| `DOCKER_WAIT_SECONDS` | `60` | how long to wait for Docker Desktop and the database at start |
| `DOCKER_CMD_TIMEOUT` | `30` | time limit for each quick Docker call (seconds) |
| `STEP_TIMEOUT_SECONDS` | `3600` | time limit for each long step: dump, verification, archive, upload |
| `MAX_RUN_SECONDS` | `10800` | time limit for the whole run (3 hours). Raise it together with `STEP_TIMEOUT_SECONDS` when the database has grown |
| `DUMP_LOCK_WAIT_SECONDS` | `120` | how long `pg_dump` waits for a table lock (for example held by a migration) before giving up with an error |
| `MIN_FREE_MB` | `1024` | free disk space needed is the larger of this and twice the last backup's size |
| `RESTORE_CHECK_MAX_AGE_HOURS` | `36` | `restore-check.sh` fails if the newest dump is older |
| `RESTORE_CHECK_IMAGE` | image of the DB container | Postgres image for the disposable restore container |
| `RESTORE_CONTAINER_MAX_SECONDS` | `4200` | the disposable container stops itself after this long, whatever happens (even with sessions still open) |
| `RESTORE_CONTAINER_MEMORY` | `2g` | memory limit of the disposable container (for example `512m`, `4g`) |
| `RESTORE_CONTAINER_CPUS` | `2` | CPU limit of the disposable container (for example `1` or `0.5`) |
| `RESTORE_CONTAINER_PIDS` | `256` | limit on the number of processes in the disposable container (at least 30; Postgres itself needs a couple of dozen) |
| `RESTORE_MIN_FREE_MB` | `2048` | the check refuses to restore unless Docker has at least this much free disk space, or five times the dump's size plus 512 MB if that is more |
| `EXTRA_PATH` | empty | extra folders to search for `docker` / `rclone` first |

### Results and log levels

* **Exit status of the backup:** `0` every stage succeeded; `1` something failed
  (this includes "the lock could not be used at all"); `2` bad configuration; `75`
  another backup really was running and held the lock (nothing was done and
  healthchecks.io was not told); `124` stopped by the 3-hour limit; `129`, `130` or
  `143` stopped by SIGHUP (terminal closed), SIGINT (Ctrl-C) or SIGTERM (shutdown,
  `launchctl bootout`). (`restore-check.sh`: `0` means all checks passed, `1` a
  check failed, `2` bad usage or configuration, `129`/`130`/`143` as above.)
* **Log levels:** `INFO` normal, `SKIP` something you have not configured, `WARN`
  worth knowing but not a failure, `ERROR` a failure, and in the restore check
  `PASS` / `FAIL`.
* **healthchecks.io messages:** `/start` when a run begins, the plain URL on
  success (with the one-line summary), `/fail` on failure with the last 40 log
  lines attached. A failed ping to healthchecks.io never changes the result of the
  backup itself, but an answer other than `OK` is reported as a warning.

### Housekeeping

* The scripts only ever delete files named exactly like `spine-db-<time>.dump` or
  `spine-media-<time>.tar.gz` in the `nightly` and `monthly` folders.
  **Deployment dumps are kept forever**: check them now and then with
  `ls -lh ~/projects/spine-backups/pre-deploy-*.dump` and delete old ones by hand.
* A backup that was interrupted by a power cut or `kill -9` can leave a hidden
  temporary file called `.partial-...` in the backup folders. The next run removes
  it once it is a day old. (A run that is stopped normally cleans up by itself.)
* Hidden bookkeeping files in the `nightly` folder: `.suspect-backups` (the names
  of backups that were flagged by the sanity checks, so they never serve as the
  size yardstick) and, after an `ACCEPT_SMALLER_DB=1` run, `.size-baseline-from`.
  Never pruned, never uploaded, tiny; leave them alone.
* The scripts' temporary files in `$TMPDIR` are removed by the run that made
  them. After a `kill -9` the next run sweeps any that are more than an hour old.
* Databases named `spine_before_restore_...`, `spine_restore_...` or
  `spine_rejected_...` are left by disaster recovery; Part J shows how to list
  and delete them.

### Security notes

* Backups contain **everything** users have entered, including email addresses and
  password hashes. The backup folder is created readable only by you (`0700`,
  files `0600`); keep it that way and do not sync it to a shared folder.
* Keep the R2 bucket **private**. The token is limited to one bucket and to object
  read/write. Anyone who gets the server's `rclone.conf` could read or delete the
  off-site copies, so protect the Mac's login. R2 stores data encrypted at rest.
  If you want copies that even Cloudflare cannot read, rclone can add its own
  encryption layer (its "crypt" remote). That is beyond this page, and if you lose
  the crypt password the backups are unreadable, so only do it if you can keep that
  password safe.
* **Optional extra protection: an R2 bucket lock.** The token can also delete
  objects, so someone who broke into the server could delete the off-site copies.
  In the bucket's *Settings* tab, *Bucket lock rules* → *Add rule* lets you make
  objects impossible to delete or overwrite for a number of days, even for
  someone holding a valid token. If you use it, choose a period shorter than the
  30-day expiry rule (14 days is a good choice), turn it on only after the whole
  setup works, and afterwards never rename or re-copy files in the `nightly`
  folder by hand: a changed file would be uploaded again and the lock would
  refuse to overwrite the earlier copy.
* The healthchecks.io URL works like a password (see Step 6). The script never
  prints it: it is kept off the process list and command lines, and stripped from
  anything it logs or sends.
* `.env.production` is deliberately **not** backed up (see
  [What is NOT backed up](#what-is-not-backed-up-read-this)).
