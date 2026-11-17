"""Bound forms that restrict publisher membership and approved selections."""

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import Article, Newsletter, Publisher, User


class ArticleForm(forms.ModelForm):
    """Collect article content while preserving authorship rules."""

    class Meta:
        """Declare model metadata or serializer/form field policy."""

        model = Article
        fields = ("title", "content", "publisher")
        widgets = {
            "title": forms.TextInput(
                attrs={"placeholder": "Enter your headline"},
            ),
            "content": forms.Textarea(
                attrs={"rows": 12},
            ),
        }

    def __init__(self, *args, user, **kwargs):
        """Initialise fields and restrict choices for the current account."""
        super().__init__(*args, **kwargs)

        # New articles belong to the signed-in journalist.
        if not self.instance.pk:
            self.instance.author = user

        # Only offer publishers that the article's author belongs to.
        self.fields["publisher"].queryset = Publisher.objects.filter(
            journalists__pk=self.instance.author_id,
        ).distinct()

        self.fields["publisher"].empty_label = (
            "Independent article — no publisher"
        )
        self.fields["publisher"].help_text = (
            "Choose a publisher the author belongs to, "
            "or leave this article independent."
        )


class NewsletterForm(forms.ModelForm):
    """Let journalists and editors curate a newsletter from approved articles."""

    class Meta:
        """Declare model metadata or serializer/form field policy."""

        model = Newsletter
        fields = ("title", "description", "publisher", "articles")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "articles": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, user, **kwargs):
        """Initialise fields and restrict choices for the current account."""
        super().__init__(*args, **kwargs)

        if not self.instance.pk:
            self.instance.author = user

        # A newsletter must contain at least one approved article.
        self.fields["articles"].required = True
        self.fields["articles"].queryset = Article.objects.filter(
            approved=True,
        ).select_related("author", "publisher")

        # Only offer publishers that the newsletter's author belongs to.
        membership = (
            "editors__pk"
            if self.instance.author.role == User.Role.EDITOR
            else "journalists__pk"
        )
        self.fields["publisher"].queryset = Publisher.objects.filter(
            **{membership: self.instance.author_id}
        ).distinct()

        self.fields["publisher"].empty_label = (
            "Independent newsletter — no publisher"
        )


class SubscriptionForm(forms.ModelForm):
    """Let readers manage their publisher and journalist subscriptions."""

    class Meta:
        """Declare model metadata or serializer/form field policy."""

        model = User
        fields = (
            "subscribed_publishers",
            "subscribed_journalists",
        )
        widgets = {
            "subscribed_publishers": forms.CheckboxSelectMultiple(),
            "subscribed_journalists": forms.CheckboxSelectMultiple(),
        }
        labels = {
            "subscribed_publishers": "Publishers",
            "subscribed_journalists": "Journalists",
        }

    def __init__(self, *args, **kwargs):
        """Initialise fields and restrict choices for the current account."""
        super().__init__(*args, **kwargs)

        self.fields["subscribed_publishers"].queryset = (
            Publisher.objects.order_by("name")
        )

        # Readers may follow active journalists, not readers or editors.
        self.fields["subscribed_journalists"].queryset = User.objects.filter(
            role=User.Role.JOURNALIST,
            is_active=True,
        ).order_by("username")

        # Leaving both selections empty means unsubscribing from everyone.
        self.fields["subscribed_publishers"].required = False
        self.fields["subscribed_journalists"].required = False


class RegistrationForm(UserCreationForm):
    """Create a news account with validated passwords and a selected role.

    Only the listed public fields are writable. Staff status, superuser status
    and arbitrary permission assignments are never accepted from sign-up.
    """

    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        """Use the project's custom user and its three supported roles."""

        model = User
        fields = ("username", "email", "role")


class PublisherForm(forms.ModelForm):
    """Let a publication's editors manage details and active newsroom members.

    An editor who creates a publisher is automatically retained as a member.
    This prevents accidental removal of their own management access.
    """

    class Meta:
        """Expose publication details and affiliations, without account roles."""

        model = Publisher
        fields = ("name", "description", "editors", "journalists")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "editors": forms.CheckboxSelectMultiple(),
            "journalists": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, user, **kwargs):
        """Restrict membership choices to active users with matching roles."""
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["editors"].queryset = User.objects.filter(
            role=User.Role.EDITOR, is_active=True
        ).order_by("username")
        self.fields["journalists"].queryset = User.objects.filter(
            role=User.Role.JOURNALIST, is_active=True
        ).order_by("username")
        self.fields["editors"].required = False
        self.fields["editors"].help_text = (
            "Selected editors can manage this publication. You remain an editor."
        )

    def save(self, commit=True):
        """Persist the publication and retain the acting editor's membership."""
        publisher = super().save(commit=commit)
        if commit:
            publisher.editors.add(self.user)
        return publisher
