"""Management command to initialise permissions and reconcile existing users."""

from django.core.management.base import BaseCommand

from news.models import User
from news.roles import configure_role_groups, sync_user_role


class Command(BaseCommand):
    """Refresh role permissions and reconcile all existing users.

    Configure the groups first so each user is assigned to a group
    with the current permissions. Iterate over accounts without
    loading them all into memory, then report success.
    """
    help = "Configure news role groups and synchronise existing users."

    def handle(self, *args, **options):
        """Assign current permissions and synchronise every existing account."""
        configure_role_groups()

        # Also brings users created before this command into the role system.
        for user in User.objects.iterator():
            sync_user_role(user)

        self.stdout.write(
            self.style.SUCCESS(
                "Role groups and permissions configured successfully."
            )
        )
