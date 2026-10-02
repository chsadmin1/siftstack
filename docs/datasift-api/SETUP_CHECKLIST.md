# Setup Checklist - DataSift API

One page from zero to working integration. Full version: `docs/00-quickstart.md`.

## Required

- [ ] Plan above Professional (Open API keys are gated to these plans)
- [ ] Generate an Open API key in the REISift app under your account's integration settings
- [ ] Store the key in an environment variable (`DATASIFT_API_KEY`), never in code or git
- [ ] Confirm auth: `curl https://apiv2.reisift.io/api/internal/user/ -H "Authorization: Api-Key $DATASIFT_API_KEY"` returns your user profile
- [ ] Confirm data access: `GET /api/internal/property/?limit=1` returns `count` and one record
- [ ] Confirm SiftMap: `GET https://map.reisift.io/filters/` returns your saved filters

## Per integration

- [ ] One key per integration, issued for a user with the minimum permissions the job needs
- [ ] Every write path ends with a verification read (see `docs/02-conventions.md`)
- [ ] Bulk imports dedupe with `POST /api/internal/property/exists/` before creating
- [ ] Names use plain ASCII hyphens (the platform strips em dashes from titles)

## If a key is ever exposed

- [ ] Rotate or revoke it immediately from the same integration settings screen
- [ ] Issue a replacement and update the environment variable
