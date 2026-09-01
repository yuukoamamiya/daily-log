"""Domain errors that are safe to display to a local user."""


class DailyLogError(RuntimeError):
    pass


class ValidationError(DailyLogError, ValueError):
    pass


class NotFoundError(DailyLogError):
    pass


class ConflictError(DailyLogError):
    pass
