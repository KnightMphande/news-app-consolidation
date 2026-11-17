"""Repeatable role-group setup and user membership synchronisation."""

from django.contrib.auth.models import Group, Permission

from .models import User

ROLE_GROUPS = {
    User.Role.READER: "Reader",
    User.Role.EDITOR: "Editor",
    User.Role.JOURNALIST: "Journalist",
}

ROLE_ACTIONS = {
    User.Role.READER: ("view",),
    User.Role.EDITOR: ("view", "change", "delete"),
    User.Role.JOURNALIST: ("add", "view", "change", "delete"),
}


def configure_role_groups():
    """Create the role groups and assign their required permissions."""
    for role, group_name in ROLE_GROUPS.items():
        group, _ = Group.objects.get_or_create(name=group_name)

        codenames = [
            f"{action}_{model_name}"
            for action in ROLE_ACTIONS[role]
            for model_name in ("article", "newsletter")
        ]

        if role == User.Role.EDITOR:
            # The newsletter-specific requirement permits editor creation.
            codenames.extend(("approve_article", "add_newsletter"))

        permissions = Permission.objects.filter(
            content_type__app_label="news",
            codename__in=codenames,
        )

        # Replace this group's permissions so rerunning setup is predictable.
        group.permissions.set(permissions)


def sync_user_role(user):
    """Keep a user's role group consistent with their selected role."""
    group_name = ROLE_GROUPS[user.role]
    role_group, _ = Group.objects.get_or_create(name=group_name)

    # Remove previous news roles while preserving unrelated group membership.
    previous_roles = user.groups.filter(name__in=ROLE_GROUPS.values())
    user.groups.remove(*previous_roles)
    user.groups.add(role_group)

    # Only readers should retain reader subscription relationships.
    if user.role != User.Role.READER:
        user.subscribed_publishers.clear()
        user.subscribed_journalists.clear()

    # Permissions may already have been checked on this user instance.
    for cache_name in (
        "_perm_cache",
        "_group_perm_cache",
        "_user_perm_cache",
    ):
        user.__dict__.pop(cache_name, None)
