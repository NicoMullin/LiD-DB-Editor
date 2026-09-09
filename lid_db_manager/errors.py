"""Exception types shared across the package."""


class ModManagerError(Exception):
    """Base class for every error this tool raises deliberately."""


class ModLoadError(ModManagerError):
    """A mod folder could not be parsed into a Mod."""

    def __init__(self, mod_ref: str, reason: str):
        self.mod_ref = mod_ref
        self.reason = reason
        super().__init__(f"{mod_ref}: {reason}")


class ValidationError(ModManagerError):
    """A mod failed pre-apply validation."""

    def __init__(self, mod_id: str, reason: str):
        self.mod_id = mod_id
        self.reason = reason
        super().__init__(f"{mod_id}: {reason}")


class ApplyError(ModManagerError):
    """A patch blew up mid-apply. The transaction is rolled back."""

    def __init__(self, mod_id: str, reason: str):
        self.mod_id = mod_id
        self.reason = reason
        super().__init__(f"{mod_id}: {reason}")


class RevertError(ModManagerError):
    """A snapshot could not be restored."""
