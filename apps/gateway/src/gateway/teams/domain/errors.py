"""Domain errors for teams — zero framework imports."""


class TeamError(Exception):
    """Base for team domain failures."""


class TeamNotFoundError(TeamError):
    """Team not found (or belongs to another tenant — identical response, no leak)."""


class TeamExistsError(TeamError):
    """A team with this name already exists in the tenant."""


class MemberExistsError(TeamError):
    """User is already a member of this team."""


class MemberNotFoundError(TeamError):
    """User is not a member of this team."""


class UserNotFoundError(TeamError):
    """User does not exist in the tenant."""
