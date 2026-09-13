"""Exception types, in their own module so every other module can import them
without creating a cycle.

`container` re-exports both names, so `from owlg.container import OwlgError`
keeps working for code written against earlier versions.
"""


class OwlgError(Exception):
    """Any error raised by this library that is the caller's to handle."""


class NeedKey(OwlgError):
    """The file is encrypted and no passphrase (or the wrong one) was supplied.

    Callers dispatch on this to prompt for a passphrase, so every code path that
    opens an encrypted file must raise it -- both the flat (v3) and the tiled
    (v4) reader.
    """
