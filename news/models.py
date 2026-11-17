"""Normalised news entities, relationship constraints, and content validation."""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class User(AbstractUser):
    """Extend Django's user with a news role and reader subscriptions."""

    class Role(models.TextChoices):
        """Canonical role values used by groups, forms and API checks."""

        READER = "reader", "Reader"
        EDITOR = "editor", "Editor"
        JOURNALIST = "journalist", "Journalist"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.READER,
    )

    # A reader may subscribe to several publishers.
    subscribed_publishers = models.ManyToManyField(
        "Publisher",
        blank=True,
        related_name="subscribers",
    )

    # This relationship connects readers to journalist user accounts.
    subscribed_journalists = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="subscribers",
        limit_choices_to={"role": "journalist"},
    )

    @property
    def independent_articles(self):
        """Return this journalist's articles without a publisher."""
        return self.articles.filter(publisher__isnull=True)

    @property
    def independent_newsletters(self):
        """Return this journalist's newsletters without a publisher."""
        return self.newsletters.filter(publisher__isnull=True)

    def __str__(self):
        """Return a readable label for templates and administrative lists."""
        return f"{self.username} ({self.get_role_display()})"


class Publisher(models.Model):
    """A news organisation with editors and journalists."""

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)

    editors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="publishers_as_editor",
        limit_choices_to={"role": "editor"},
    )

    journalists = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="publishers_as_journalist",
        limit_choices_to={"role": "journalist"},
    )

    class Meta:
        """Declare model metadata or serializer/form field policy."""

        ordering = ["name"]

    def __str__(self):
        """Return a readable label for templates and administrative lists."""
        return self.name


class Article(models.Model):
    """An article that must be approved before readers can access it."""

    title = models.CharField(max_length=250)
    content = models.TextField()

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="articles",
        limit_choices_to={"role": "journalist"},
    )

    # No publisher means the journalist is publishing independently.
    publisher = models.ForeignKey(
        Publisher,
        on_delete=models.PROTECT,
        related_name="articles",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved = models.BooleanField(default=False)

    class Meta:
        """Declare model metadata or serializer/form field policy."""

        ordering = ["-created_at", "-pk"]
        permissions = [
            ("approve_article", "Can approve article"),
        ]

    def clean(self):
        """Validate authorship when a form or service validates the model."""
        super().clean()

        if not self.author_id:
            return

        if self.author.role != User.Role.JOURNALIST:
            raise ValidationError(
                {"author": "An article's author must be a journalist."}
            )

        # Publisher articles must come from that publisher's journalists.
        if self.publisher_id:
            is_member = self.publisher.journalists.filter(
                pk=self.author_id
            ).exists()

            if not is_member:
                raise ValidationError(
                    {
                        "publisher": (
                            "This journalist does not belong to "
                            "the selected publisher."
                        )
                    }
                )

    def __str__(self):
        """Return a readable label for templates and administrative lists."""
        return self.title


class Newsletter(models.Model):
    """A curated collection of articles."""

    title = models.CharField(max_length=250)
    description = models.TextField(blank=True)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="newsletters",
        limit_choices_to={"role__in": ["journalist", "editor"]},
    )

    publisher = models.ForeignKey(
        Publisher,
        on_delete=models.PROTECT,
        related_name="newsletters",
        null=True,
        blank=True,
    )

    articles = models.ManyToManyField(
        Article,
        blank=True,
        related_name="newsletters",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Declare model metadata or serializer/form field policy."""

        ordering = ["-created_at", "-pk"]

    def clean(self):
        """Check that the author can curate this newsletter."""
        super().clean()

        if not self.author_id:
            return

        if self.author.role not in (
            User.Role.JOURNALIST,
            User.Role.EDITOR,
        ):
            raise ValidationError(
                {
                    "author": (
                        "A newsletter's author must be "
                        "a journalist or editor."
                    )
                }
            )

        if self.publisher_id:
            if self.author.role == User.Role.JOURNALIST:
                members = self.publisher.journalists
            else:
                members = self.publisher.editors

            if not members.filter(pk=self.author_id).exists():
                raise ValidationError(
                    {
                        "publisher": (
                            "This author does not belong to "
                            "the selected publisher."
                        )
                    }
                )

    def __str__(self):
        """Return a readable label for templates and administrative lists."""
        return self.title


class ApprovalLog(models.Model):
    """Record approved articles received by our internal API."""

    # One record per article makes repeated API requests harmless.
    article = models.OneToOneField(
        Article,
        on_delete=models.CASCADE,
        related_name="approval_log",
    )
    received_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Return a readable label for templates and administrative lists."""
        return f"Approval received: {self.article.title}"
