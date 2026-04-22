"""User profile service — signup flow stores email_address, legacy code expects email."""

_USERS = {99: {"username": "jdoe", "email_address": "new-signup@acme.test"}}





def load_user_profile(user_id):
    rec = _USERS[int(user_id)]
    _ = rec.get("username", "")
    return rec["email"]
