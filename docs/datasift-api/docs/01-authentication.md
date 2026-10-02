# Authentication

All DataSift API access authenticates with a **REISift Open API key** sent in the `Authorization` header using the `Api-Key` scheme:

```
Authorization: Api-Key YOUR_OPEN_API_KEY
```

Example:

```bash
curl https://apiv2.reisift.io/api/internal/property/ \
  -H "Authorization: Api-Key YOUR_OPEN_API_KEY"
```

The same key and scheme work on both API surfaces:

| API | Base URL |
|---|---|
| DataSift API Core | `https://apiv2.reisift.io` |
| SiftMap API | `https://map.reisift.io` |

## Getting a key

Open API keys are available on plans above Professional. Generate and manage keys from your account's **integration settings** in the REISift app.

## Permissions model

Each key acts on behalf of the user it was issued for and inherits that user's permissions. A key issued for a standard team member can do what that member can do in the app; a key issued for an admin carries admin permissions. Issue keys at the lowest permission level that gets the job done, and issue separate keys for separate integrations so each can be revoked independently.

## Key hygiene

- Send the key only in the `Authorization` header, never in a URL query string.
- Store it in an environment variable or a secrets manager, never in source code or a git repository.
- Rotate or revoke a key immediately from the integration settings screen if it is ever exposed (pasted into a chat, committed, logged).
- One integration, one key. When you retire an integration, revoke its key.

## Recommended headers

```
Authorization: Api-Key YOUR_OPEN_API_KEY
Accept: application/json
Content-Type: application/json        (on requests with a body)
```

## Failure modes

| Response | Meaning |
|---|---|
| 401 | Missing or malformed Authorization header, or the key is invalid or revoked |
| 403 | The key is valid but the user behind it lacks permission for this resource, or the plan does not include this capability |
| 404 | Wrong path, or the resource exists but is outside this account's scope |

There is no token refresh cycle and no expiry to manage. A key works until it is revoked or rotated.
