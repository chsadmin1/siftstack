# Owners, Phones, and Communication

Owner records hold the person side of a property record: names, mailing address, phones, emails, communication history, offers, and the message board.

## Owner CRUD and actions

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/owner/` | List owners |
| POST | `/api/internal/owner/` | Create an owner |
| GET | `/api/internal/owner/{uuid}/` | Owner detail |
| PATCH | `/api/internal/owner/{uuid}/` | Partial update |
| DELETE | `/api/internal/owner/{uuid}/` | Delete |
| GET | `/api/internal/owner/{uuid}/logs/` | Owner change log |
| POST | `/api/internal/owner/{uuid}/add-property/` | Link the owner to a property |
| POST | `/api/internal/owner/{uuid}/contact/` | Record a contact event |
| POST | `/api/internal/owner/{uuid}/do-not-mail-ever/` | Permanent mail suppression |
| POST | `/api/internal/owner/{uuid}/verify-email/{email}/` | Verify an email address |

## Phones and emails

Phones and emails are managed with upsert and remove actions on the owner:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/internal/owner/{uuid}/upsert-phones/` | Add or update phone numbers |
| POST | `/api/internal/owner/{uuid}/remove-phones/` | Remove phone numbers |
| POST | `/api/internal/owner/{uuid}/upsert-emails/` | Add or update emails |
| POST | `/api/internal/owner/{uuid}/remove-emails/` | Remove emails |

Phone type definitions (mobile, landline, VOIP and so on) are listed at `GET /api/internal/phone/type/`.

## Phone tags - the dial-priority system

Phone tags are account-wide labels applied to individual phone numbers (not to records). They are the backbone of dial prioritization: score numbers externally, tag them by tier, then build call queues by filtering on phone tags.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/phone/tag/` | List phone tags |
| POST | `/api/internal/phone/tag/` | Create a phone tag |
| PATCH | `/api/internal/phone/tag/{uuid}/` | Rename or edit |
| DELETE | `/api/internal/phone/tag/{uuid}/` | Delete |
| GET | `/api/internal/phone/tag/{uuid}/properties-count/` | How many records carry a phone with this tag |
| POST | `/api/internal/phone/add-phone-tag/` | Apply a tag to phone numbers |
| POST | `/api/internal/property/{uuid}/add-phone-tag/` | Apply from the property side |
| POST | `/api/internal/owner/{uuid}/add-phone-tag/` | Apply from the owner side |

The `properties-count` endpoint is your verification tool: after a bulk tagging run, compare the count against the number you intended to tag.

## SMS

Send and manage SMS per owner:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/owner/{owner_uuid}/sms/` | SMS history with this owner |
| POST | `/api/internal/owner/{owner_uuid}/sms/` | Send an SMS |
| POST | `/api/internal/owner/{owner_uuid}/sms/{uuid}/cancel/` | Cancel a queued message |
| POST | `/api/internal/owner/{owner_uuid}/sms/{uuid}/resend/` | Resend |

Sending requires a connected texting integration (see `10-integrations.md`). SMS content follows carrier compliance rules; keep opt-out handling in your workflow and respect `dnc` and `opt_out` flags on the owner.

## Message board

Internal team notes on an owner (not messages to the owner):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/owner/{owner_uuid}/message/` | List board messages |
| POST | `/api/internal/owner/{owner_uuid}/message/` | Post a message |
| PATCH | `/api/internal/owner/{owner_uuid}/message/{uuid}/` | Edit |
| DELETE | `/api/internal/owner/{owner_uuid}/message/{uuid}/` | Delete |
| POST | `/api/internal/owner/{owner_uuid}/message/{uuid}/pin/` | Pin to top |
| POST | `/api/internal/owner/{owner_uuid}/message/{uuid}/unpin/` | Unpin |

## Offers

Offers recorded against an owner are readable at `GET /api/internal/owner/{owner_uuid}/offer/` and `GET /api/internal/owner/{owner_uuid}/offer/{uuid}/`.

## Contacts (the address book)

Separate from owners, the account has a general contact book under `/api/internal/contacts/` with its own tags and tag folders: contact CRUD, `add-tags`, `remove-tags`, `status`, next/prev navigation, and per-tag `contacts-count`. The shape mirrors the property tag system; see the endpoint index for the full list.
