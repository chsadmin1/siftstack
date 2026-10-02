"""One-way email cadence for the "D x D" board.

Sibling to `sms_agent`, not a rewrite of it. Reuses its CRM bridge, sender
identity mapping (`config/sms_senders.json`) and opt-out tag ("Do Not Market")
so a suppression or an assignment change made by the texting side is honored
here automatically, and vice versa. Sends through DataSift's own connected
mailbox (`/api/internal/email-integration/`), not a separate ESP account.
"""
