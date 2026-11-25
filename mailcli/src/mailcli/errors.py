"""Errors shown to the person running mailcli.

Messages raised as MailcliError are printed as-is. They must never contain
a password, a credential, or a raw protocol trace that might echo one.
"""


class MailcliError(Exception):
    """A problem the user can act on, with a message that is safe to print."""
