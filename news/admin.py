"""Administrative data management; publish through the website approval view."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Article, Newsletter, Publisher, User
from .models import ApprovalLog
from .roles import sync_user_role


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """Manage custom users using Django's existing user admin."""

    list_display = (
        "username",
        "email",
        "role",
        "is_staff",
        "is_active",
    )
    list_filter = UserAdmin.list_filter + ("role",)

    # Include our custom fields when editing an existing account.
    fieldsets = UserAdmin.fieldsets + (
        (
            "News role and subscriptions",
            {
                "fields": (
                    "role",
                    "subscribed_publishers",
                    "subscribed_journalists",
                )
            },
        ),
    )

    # Include the role when creating a new account.
    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "News role",
            {"fields": ("email", "role")},
        ),
    )

    filter_horizontal = UserAdmin.filter_horizontal + (
        "subscribed_publishers",
        "subscribed_journalists",
    )

    def save_related(self, request, form, formsets, change):
        """Reapply role rules after admin saves many-to-many fields."""
        super().save_related(request, form, formsets, change)
        sync_user_role(form.instance)


@admin.register(Publisher)
class PublisherAdmin(admin.ModelAdmin):
    """Manage publication details and newsroom memberships."""

    list_display = ("name",)
    search_fields = ("name",)
    filter_horizontal = ("editors", "journalists")


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    """Browse article metadata while keeping publication in its workflow."""

    list_display = (
        "title",
        "author",
        "publisher",
        "approved",
        "created_at",
    )
    list_filter = ("approved", "publisher")
    search_fields = ("title", "content", "author__username")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("author", "publisher")

    def get_readonly_fields(self, request, obj=None):
        """Keep approval read-only; publication uses the dedicated view."""
        fields = list(super().get_readonly_fields(request, obj))

        # Use the website approval action so email and API delivery run too.
        # Direct checkbox edits would bypass that publication workflow.
        fields.append("approved")

        return fields

    def save_model(self, request, obj, form, change):
        """Return changed content to draft, including administrative edits.

        Approval cannot be set from the admin form. Editors publish through
        the website/API, where subscriber notifications are also triggered.
        """
        if not change or {
            "title",
            "content",
            "author",
            "publisher",
        }.intersection(form.changed_data):
            obj.approved = False
        super().save_model(request, obj, form, change)


@admin.register(Newsletter)
class NewsletterAdmin(admin.ModelAdmin):
    """Manage curated collections without copying article content."""

    list_display = ("title", "author", "publisher", "created_at")
    list_filter = ("publisher",)
    search_fields = ("title", "description", "author__username")
    readonly_fields = ("created_at", "updated_at")
    filter_horizontal = ("articles",)
    list_select_related = ("author", "publisher")


@admin.register(ApprovalLog)
class ApprovalLogAdmin(admin.ModelAdmin):
    """Inspect internal API receipts without fabricating or editing them."""

    list_display = ("article", "received_at")
    readonly_fields = ("article", "received_at")
    list_select_related = ("article",)

    def has_add_permission(self, request):
        # Logs should originate from the internal API.
        """Prevent manually fabricated internal approval records."""
        return False

    def has_change_permission(self, request, obj=None):
        """Keep internal approval records immutable in the admin."""
        return False
