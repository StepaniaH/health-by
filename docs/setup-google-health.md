# Connecting real data (GCP OAuth)

[Chinese](zh/setup-google-health.md)

Goal: switch health-by from demo data to real Fitbit / Google Health data.
Mode: **BYO (bring your own GCP project)** — each deployment uses its own OAuth client, which keeps
Google's Restricted-scope distribution review out of the picture.

## Before you start (S0)

- [ ] The Fitbit account has been migrated to a Google account — the test is that the **Fitbit app**
      on your phone signs in and shows data. Do not use the fitbit.com web panel as the check; that
      entry point changes with migration status and misleads.
- [ ] The data you want belongs to **this** Google account (the same account sees the band in the
      Fitbit app).
- [ ] The deployment machine can reach `oauth2.googleapis.com` and `health.googleapis.com` outbound
      (token exchange and data fetching both start server-side).

> This project does not do "browser export". A browser is involved twice: creating the GCP OAuth
> client, and clicking **Connect** once. After that the server syncs on its own using the refresh
> token (every 6 hours by default) with no browser involvement.

## Steps (each has a check)

### S1 · Create a project and enable the API

1. Open `console.cloud.google.com` and sign in with the Google account holding the Fitbit data.
2. Project selector → **New project**, any name (for example `health-by`). The project ID is generated;
   note it down.
3. **APIs & Services** → **Library** → search **Google Health API** → Enable.

Check: the API page shows the status as enabled.

### S2 · OAuth consent screen

1. **APIs & Services** → **OAuth consent screen** (in the newer UI: **Google Auth Platform** →
   **Branding**):
   - App name: any (for example `health-by`)
   - User support email / developer contact: your email
   - Audience: **External**
2. Keep the publishing status at **Testing** and add your own Google account under **Test users**.

Check: the Audience page shows Testing with one test user.

### S3 · Data access scopes

Under **Data Access**, select the **9 read-only** Google Health API scopes (nothing `*.writeonly`;
`reproductive_health`, `logged_symptoms`, and `mindfulness` are unrelated to this project):

```
https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
https://www.googleapis.com/auth/googlehealth.sleep.readonly
https://www.googleapis.com/auth/googlehealth.ecg.readonly
https://www.googleapis.com/auth/googlehealth.irn.readonly
https://www.googleapis.com/auth/googlehealth.location.readonly
https://www.googleapis.com/auth/googlehealth.nutrition.readonly
https://www.googleapis.com/auth/googlehealth.profile.readonly
https://www.googleapis.com/auth/googlehealth.settings.readonly
```

The code's `SCOPES` has 11 entries: the 9 health read-only scopes above plus `openid` and `profile`
(non-sensitive OIDC scopes granted automatically at runtime; they are **not** registered on the
consent screen).

Check: the Data Access page lists 9 scopes (`settings.readonly` counts as sensitive, the other eight as
restricted; the classification does not affect functionality). Click **Save**.

### S4 · Create the OAuth client

**Credentials** → Create client → type **Web application**:

- Name: `health-by web`
- Authorized redirect URI: `http://127.0.0.1:39441/oauth/callback`

> Common mistake: the URI belongs in **Authorized redirect URIs**, not in **Authorized JavaScript
> origins** — the latter accepts only `scheme://host[:port]` with no path, and putting it there fails
> with `Invalid Origin: URIs must not contain a path`. This project uses no JavaScript origins; leave
> that field empty.
>
> Why this works: `127.0.0.1` needs no public reachability. It is only where the browser returns after
> consent, and the browser is on the deployment machine. Google never connects into your machine;
> token exchange and data fetching are outbound requests from it.

Saving gives you the **Client ID / Client Secret** (the secret is shown once; **never paste it into a
chat**).

### S5 · Enter the credentials in health-by

1. Open `http://127.0.0.1:39441/settings` with the service running.
2. Enter the Client ID and secret, leave the redirect URI at its default
   `http://127.0.0.1:39441/oauth/callback`, and click **Save credentials**.
3. Click **Connect (opens Google authorization)**, pick the test user, and consent. The "unverified
   app" warning is normal in testing mode.

Check: you return to Settings showing `oauth=ok`, the data source is Google Health, and the state is
**Connected**.

### S6 · First real sync

1. In Settings click **Sync now (yesterday + today)**, or from the CLI:
   `.venv/bin/health-by sync --days 2`.
2. Look at the dashboard and the coverage section.

Check:

- [ ] The dashboard shows real steps / sleep / heart rate that match the Fitbit app (a delay of up to
      one day is expected);
- [ ] Coverage is as close to 31/31 as possible; an ungranted scope drops items — record which;
- [ ] No 401/403. A 403 means revisiting S3 scopes; a 401 means revisiting the connection and token.

### S7 · Record and finish

- [ ] Write what the real data looked like into [data-coverage.md](data-coverage.md) (which endpoints returned
      data, which were empty, field differences);
- [ ] Tick the onboarding checklist at the end of this file;
- [ ] Watch the 7-day token expiry: Settings should prompt to reconnect; one click is enough;
- [ ] For a public release, add the `LICENSE` file and fill the author and license fields in
  `pyproject.toml`.

## Appendix · Data channel options

| Channel | Use | Pros | Cons | Recommendation |
|---|---|---|---|---|
| Google Health API v4 (this project) | routine sync, long history | official channel, incremental, automatic, auditable; writes to local SQLite | one-time GCP setup; testing-mode refresh token expires every 7 days and needs one reconnect click; Restricted scopes mean personal use only | **primary** |
| Manual Fitbit account export (ZIP) | one-off history backfill | zero configuration, full raw files | manual, not incremental, different format from the API (minute-level data may differ), cannot be automated | supplementary only |
| Google Takeout | as above (includes Fitbit data depending on migration state) | as above | entry point varies with account migration, large, delayed | as above |
| Android Health Connect bridge | relay data from a Pixel | avoids Google Cloud | needs a resident app on the phone, Android only, limited source coverage | not as the primary channel |
| Third-party aggregator APIs (Terra / Spike / Vital …) | skip GCP setup | fast to connect | hands health data to a third party, against the self-hosted principle; paid | not recommended |
| Legacy Fitbit Web API | — | — | shut down 2026-10-30 | not viable |

> Conclusion: use the Google Health API as the main channel. Manual export is not "export regularly";
> at most it is a one-off backfill.

## Security notes

- Client secrets, authorization codes, and access/refresh tokens are **never pasted into a chat**; they
  go only into the local settings page, which stores them encrypted (Fernet) server-side.
- The 7-day refresh-token expiry in testing mode is Google's policy, not a bug; reconnect once on
  expiry.
- Do not share a GCP project with others. For public distribution, every deployment should create its
  own project (BYO).
