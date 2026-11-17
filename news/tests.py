"""Regression coverage for roles, ownership, subscriptions and publication."""

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Article, Newsletter, Publisher, User, ApprovalLog
from .roles import configure_role_groups
import json
from unittest.mock import Mock, patch

from django.core import mail, signing
from django.test import override_settings

from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .notifications import APPROVAL_SIGNING_SALT


class RoleAndModelTests(TestCase):
    """Check role permissions and the main content relationships."""

    @classmethod
    def setUpTestData(cls):
        # Test databases also need our role groups configured.
        """Create shared accounts and content for this test class."""
        configure_role_groups()

        cls.reader = User.objects.create_user(
            username="test_reader",
            password="TestingPass!482",
            role=User.Role.READER,
        )
        cls.editor = User.objects.create_user(
            username="test_editor",
            password="TestingPass!482",
            role=User.Role.EDITOR,
        )
        cls.journalist = User.objects.create_user(
            username="test_journalist",
            password="TestingPass!482",
            role=User.Role.JOURNALIST,
        )

        cls.publisher = Publisher.objects.create(
            name="Joburg Daily",
            description="Local news from Johannesburg.",
        )
        cls.publisher.editors.add(cls.editor)
        cls.publisher.journalists.add(cls.journalist)

    def test_users_receive_their_role_groups(self):
        """Saving a user should assign the group matching their role."""
        for user, group_name in (
            (self.reader, "Reader"),
            (self.editor, "Editor"),
            (self.journalist, "Journalist"),
        ):
            with self.subTest(role=user.role):
                self.assertTrue(user.groups.filter(name=group_name).exists())

    def test_reader_has_view_permissions_only(self):
        """Readers should not receive content editing permissions."""
        for model_name in ("article", "newsletter"):
            self.assertTrue(self.reader.has_perm(f"news.view_{model_name}"))

            for action in ("add", "change", "delete"):
                self.assertFalse(
                    self.reader.has_perm(f"news.{action}_{model_name}")
                )

        self.assertFalse(self.reader.has_perm("news.approve_article"))

    def test_editor_permissions_match_the_brief(self):
        """Editors can review existing content and approve articles."""
        for model_name in ("article", "newsletter"):
            for action in ("view", "change", "delete"):
                self.assertTrue(
                    self.editor.has_perm(f"news.{action}_{model_name}")
                )

            self.assertFalse(self.editor.has_perm("news.add_article"))

        self.assertTrue(self.editor.has_perm("news.approve_article"))

    def test_journalist_can_manage_content_but_not_approve(self):
        """Journalists can create content but cannot approve it."""
        for model_name in ("article", "newsletter"):
            for action in ("add", "view", "change", "delete"):
                self.assertTrue(
                    self.journalist.has_perm(f"news.{action}_{model_name}")
                )

        self.assertFalse(self.journalist.has_perm("news.approve_article"))

    def test_role_change_updates_groups_and_clears_subscriptions(self):
        """A former reader should lose reader subscriptions."""
        unrelated_group = Group.objects.create(name="Book Club")
        self.reader.groups.add(unrelated_group)
        self.reader.subscribed_publishers.add(self.publisher)
        self.reader.subscribed_journalists.add(self.journalist)

        self.reader.role = User.Role.JOURNALIST
        self.reader.save()

        self.assertTrue(self.reader.groups.filter(name="Journalist").exists())
        self.assertFalse(self.reader.groups.filter(name="Reader").exists())

        # Changing a news role should preserve unrelated groups.
        self.assertTrue(self.reader.groups.filter(name="Book Club").exists())
        self.assertFalse(self.reader.subscribed_publishers.exists())
        self.assertFalse(self.reader.subscribed_journalists.exists())

    def test_independent_article_starts_unapproved(self):
        """Independent work should have no publisher and await approval."""
        article = Article(
            title="Community garden opens",
            content="Residents have opened a new community garden.",
            author=self.journalist,
        )
        article.full_clean()
        article.save()

        self.assertFalse(article.approved)
        self.assertIsNone(article.publisher)
        self.assertIn(
            article,
            self.journalist.independent_articles,
        )

    def test_publisher_article_rejects_nonmember_journalist(self):
        """A journalist must belong to the selected publisher."""
        outsider = User.objects.create_user(
            username="outside_journalist",
            password="TestingPass!482",
            role=User.Role.JOURNALIST,
        )
        article = Article(
            title="Publisher article",
            content="An article submitted by a nonmember.",
            author=outsider,
            publisher=self.publisher,
        )

        with self.assertRaises(ValidationError) as error:
            article.full_clean()

        self.assertIn("publisher", error.exception.message_dict)

    def test_newsletter_can_collect_multiple_articles(self):
        """A newsletter should retain its selected article relationships."""
        first_article = Article.objects.create(
            title="First story",
            content="First story content.",
            author=self.journalist,
            approved=True,
        )
        second_article = Article.objects.create(
            title="Second story",
            content="Second story content.",
            author=self.journalist,
            approved=True,
        )

        newsletter = Newsletter(
            title="Weekly roundup",
            description="This week's local stories.",
            author=self.journalist,
        )
        newsletter.full_clean()
        newsletter.save()
        newsletter.articles.add(first_article, second_article)

        self.assertEqual(newsletter.articles.count(), 2)
        self.assertIn(
            newsletter,
            self.journalist.independent_newsletters,
        )


class ArticlePageTests(TestCase):
    """Verify that page access protects unpublished content."""

    @classmethod
    def setUpTestData(cls):
        """Create shared accounts and content for this test class."""
        configure_role_groups()

        cls.reader = User.objects.create_user(
            username="page_reader",
            role=User.Role.READER,
        )
        cls.editor = User.objects.create_user(
            username="page_editor",
            role=User.Role.EDITOR,
        )
        cls.journalist = User.objects.create_user(
            username="page_journalist",
            role=User.Role.JOURNALIST,
        )
        cls.other_journalist = User.objects.create_user(
            username="other_journalist",
            role=User.Role.JOURNALIST,
        )

        cls.draft = Article.objects.create(
            title="Private draft",
            content="Unpublished content.",
            author=cls.journalist,
        )
        cls.published = Article.objects.create(
            title="Published story",
            content="Publicly approved content.",
            author=cls.other_journalist,
            approved=True,
        )

    def test_anonymous_user_is_redirected_to_login(self):
        """Verify that anonymous user is redirected to login."""
        response = self.client.get("/")
        self.assertRedirects(
            response,
            "/accounts/login/?next=/",
        )

    def test_reader_sees_published_content_but_not_drafts(self):
        """Verify that reader sees published content but not drafts."""
        self.client.force_login(self.reader)
        response = self.client.get("/")

        self.assertContains(response, self.published.title)
        self.assertNotContains(response, self.draft.title)

        response = self.client.get(f"/articles/{self.draft.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_journalist_can_view_own_draft(self):
        """Verify that journalist can view own draft."""
        self.client.force_login(self.journalist)
        response = self.client.get(f"/articles/{self.draft.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.draft.title)

    def test_other_journalist_cannot_view_another_authors_draft(self):
        """Verify that other journalist cannot view another authors draft."""
        self.client.force_login(self.other_journalist)
        response = self.client.get(f"/articles/{self.draft.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_editor_can_view_pending_article(self):
        """Verify that editor can view pending article."""
        self.client.force_login(self.editor)
        response = self.client.get(f"/articles/{self.draft.pk}/")
        self.assertEqual(response.status_code, 200)

    @patch("news.views.notify_article_approval", return_value=[])
    def test_editor_can_approve_article(self, mock_notify):
        """An editor's approval should make the article visible to readers."""
        self.client.force_login(self.editor)

        response = self.client.post(f"/articles/{self.draft.pk}/approve/")

        self.assertRedirects(
            response,
            f"/articles/{self.draft.pk}/",
        )

        self.draft.refresh_from_db()
        self.assertTrue(self.draft.approved)

        self.client.force_login(self.reader)
        response = self.client.get(f"/articles/{self.draft.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_reader_and_journalist_cannot_approve(self):
        """Content authors and readers must not approve articles."""
        for user in (self.reader, self.journalist):
            with self.subTest(role=user.role):
                self.client.force_login(user)

                response = self.client.post(
                    f"/articles/{self.draft.pk}/approve/"
                )

                self.assertEqual(response.status_code, 403)
                self.draft.refresh_from_db()
                self.assertFalse(self.draft.approved)

    def test_approval_requires_post(self):
        """Opening an approval URL must not publish the article."""
        self.client.force_login(self.editor)

        response = self.client.get(f"/articles/{self.draft.pk}/approve/")

        self.assertEqual(response.status_code, 405)
        self.draft.refresh_from_db()
        self.assertFalse(self.draft.approved)


@override_settings(
    MAILERS={
        "default": {
            "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
        },
    },
)
class ApprovalNotificationTests(TestCase):
    """Test notification delivery and the signed internal endpoint."""

    @classmethod
    def setUpTestData(cls):
        """Create shared accounts and content for this test class."""
        configure_role_groups()

        cls.editor = User.objects.create_user(
            username="notification_editor",
            role=User.Role.EDITOR,
        )
        cls.journalist = User.objects.create_user(
            username="notification_journalist",
            role=User.Role.JOURNALIST,
        )
        cls.reader = User.objects.create_user(
            username="notification_reader",
            email="subscriber@example.com",
            role=User.Role.READER,
        )

        cls.publisher = Publisher.objects.create(
            name="Notification Daily",
        )
        cls.publisher.journalists.add(cls.journalist)

        # Subscribe through both routes to check email deduplication.
        cls.reader.subscribed_publishers.add(cls.publisher)
        cls.reader.subscribed_journalists.add(cls.journalist)

        cls.article = Article.objects.create(
            title="Notification test story",
            content="The full article content.",
            author=cls.journalist,
            publisher=cls.publisher,
        )

    def test_unsigned_event_is_rejected(self):
        """Verify that unsigned event is rejected."""
        response = self.client.post(
            "/api/approved/",
            data=json.dumps({"article_id": self.article.pk}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ApprovalLog.objects.exists())

    def test_signed_event_creates_only_one_log(self):
        """Verify that signed event creates only one log."""
        self.article.approved = True
        self.article.save()

        event = signing.dumps(
            {"article_id": self.article.pk},
            salt=APPROVAL_SIGNING_SALT,
        )

        for expected_status in (201, 200):
            response = self.client.post(
                "/api/approved/",
                data=json.dumps({"event": event}),
                content_type="application/json",
            )
            self.assertEqual(
                response.status_code,
                expected_status,
            )

        self.assertEqual(ApprovalLog.objects.count(), 1)

    @patch("news.notifications.requests.post")
    def test_approval_sends_email_and_posts_event_once(self, mock_post):
        # Route the mocked HTTP call through Django's test client.
        # This exercises the endpoint without needing a running server.
        """Verify that approval sends email and posts event once."""

        def deliver_event(url, **kwargs):
            """Deliver event."""
            response = self.client.post(
                "/api/approved/",
                data=json.dumps(kwargs["json"]),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 201)

            result = Mock()
            result.status_code = response.status_code
            result.raise_for_status.return_value = None
            return result

        mock_post.side_effect = deliver_event
        self.client.force_login(self.editor)

        approval_url = f"/articles/{self.article.pk}/approve/"
        response = self.client.post(approval_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].to,
            ["subscriber@example.com"],
        )
        self.assertIn(self.article.content, mail.outbox[0].body)
        self.assertEqual(ApprovalLog.objects.count(), 1)
        mock_post.assert_called_once()

        # Approving an already approved article must not resend.
        self.client.post(approval_url)

        self.assertEqual(len(mail.outbox), 1)
        mock_post.assert_called_once()


class ArticleWritingTests(TestCase):
    """Verify article creation, ownership and the editing permissions."""

    @classmethod
    def setUpTestData(cls):
        """Create shared accounts and content for this test class."""
        configure_role_groups()

        cls.journalist = User.objects.create_user(
            username="writer",
            role=User.Role.JOURNALIST,
        )
        cls.other_journalist = User.objects.create_user(
            username="other_writer",
            role=User.Role.JOURNALIST,
        )
        cls.editor = User.objects.create_user(
            username="writing_editor",
            role=User.Role.EDITOR,
        )
        cls.reader = User.objects.create_user(
            username="writing_reader",
            role=User.Role.READER,
        )

        cls.article = Article.objects.create(
            title="TOKOLOSHE STOLE MY WI-FI!",
            content="A fictional resident blames a tokoloshe for slow internet.",
            author=cls.journalist,
            approved=True,
        )

    def test_journalist_creates_unapproved_article(self):
        """Verify that journalist creates unapproved article."""
        self.client.force_login(self.journalist)

        response = self.client.post(
            "/articles/create/",
            {
                "title": "TOKOLOSHE DEMANDS LOAD-SHEDDING REFUND!",
                "content": "A fictional tokoloshe wants its electricity money back.",
                "publisher": "",
                # Extra submitted fields must not override these values.
                "approved": "True",
                "author": self.other_journalist.pk,
            },
        )

        article = Article.objects.get(
            title="TOKOLOSHE DEMANDS LOAD-SHEDDING REFUND!",
        )

        self.assertRedirects(
            response,
            f"/articles/{article.pk}/",
        )
        self.assertEqual(article.author, self.journalist)
        self.assertFalse(article.approved)
        self.assertIsNone(article.publisher)

    def test_reader_and_editor_cannot_create_articles(self):
        """Verify that reader and editor cannot create articles."""
        for user in (self.reader, self.editor):
            with self.subTest(role=user.role):
                self.client.force_login(user)

                response = self.client.post(
                    "/articles/create/",
                    {
                        "title": "Blocked article",
                        "content": "This should not be created.",
                        "publisher": "",
                    },
                )

                self.assertEqual(response.status_code, 403)

        self.assertFalse(
            Article.objects.filter(title="Blocked article").exists()
        )

    def test_author_edit_requires_new_approval(self):
        """Verify that author edit requires new approval."""
        self.client.force_login(self.journalist)

        response = self.client.post(
            f"/articles/{self.article.pk}/edit/",
            {
                "title": "TOKOLOSHE RETURNS WI-FI—PASSWORD CHANGED!",
                "content": "The fictional mystery continues.",
                "publisher": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertFalse(self.article.approved)
        self.assertEqual(self.article.author, self.journalist)

    def test_journalist_cannot_edit_another_authors_article(self):
        """Verify that journalist cannot edit another authors article."""
        self.client.force_login(self.other_journalist)

        response = self.client.post(
            f"/articles/{self.article.pk}/edit/",
            {
                "title": "Unauthorised change",
                "content": "This must not replace the article.",
                "publisher": "",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "TOKOLOSHE STOLE MY WI-FI!")

    def test_editor_can_edit_existing_article(self):
        """Verify that editor can edit existing article."""
        self.client.force_login(self.editor)

        response = self.client.post(
            f"/articles/{self.article.pk}/edit/",
            {
                "title": "TOKOLOSHE WI-FI CLAIM UNDER INVESTIGATION!",
                "content": "An editor has corrected the fictional report.",
                "publisher": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertFalse(self.article.approved)
        self.assertEqual(self.article.author, self.journalist)

    def test_journalist_cannot_select_unrelated_publisher(self):
        """Verify that journalist cannot select unrelated publisher."""
        publisher = Publisher.objects.create(name="Unrelated News")
        self.client.force_login(self.journalist)

        response = self.client.post(
            "/articles/create/",
            {
                "title": "Rejected publisher submission",
                "content": "The journalist is not a publisher member.",
                "publisher": publisher.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "publisher",
            response.context["form"].errors,
        )
        self.assertFalse(
            Article.objects.filter(
                title="Rejected publisher submission",
            ).exists()
        )


class ArticleDeletionTests(TestCase):
    """Verify deletion permissions and the confirmation step."""

    @classmethod
    def setUpTestData(cls):
        """Create shared accounts and content for this test class."""
        configure_role_groups()

        cls.journalist = User.objects.create_user(
            username="delete_writer",
            role=User.Role.JOURNALIST,
        )
        cls.other_journalist = User.objects.create_user(
            username="other_delete_writer",
            role=User.Role.JOURNALIST,
        )
        cls.editor = User.objects.create_user(
            username="delete_editor",
            role=User.Role.EDITOR,
        )
        cls.reader = User.objects.create_user(
            username="delete_reader",
            role=User.Role.READER,
        )

        cls.article = Article.objects.create(
            title="TOKOLOSHE EVICTED FOR NOT PAYING RENT!",
            content="A fictional landlord has had enough.",
            author=cls.journalist,
        )

    def test_confirmation_page_does_not_delete_article(self):
        """Viewing the confirmation page must preserve the article."""
        self.client.force_login(self.journalist)

        response = self.client.get(f"/articles/{self.article.pk}/delete/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "news/article_confirm_delete.html",
        )
        self.assertTrue(Article.objects.filter(pk=self.article.pk).exists())

    def test_author_can_delete_own_article(self):
        """Verify that author can delete own article."""
        self.client.force_login(self.journalist)

        response = self.client.post(f"/articles/{self.article.pk}/delete/")

        self.assertRedirects(response, "/")
        self.assertFalse(Article.objects.filter(pk=self.article.pk).exists())

    def test_editor_can_delete_article(self):
        """Verify that editor can delete article."""
        self.client.force_login(self.editor)

        response = self.client.post(f"/articles/{self.article.pk}/delete/")

        self.assertRedirects(response, "/")
        self.assertFalse(Article.objects.filter(pk=self.article.pk).exists())

    def test_reader_cannot_delete_article(self):
        """Verify that reader cannot delete article."""
        self.client.force_login(self.reader)

        response = self.client.post(f"/articles/{self.article.pk}/delete/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Article.objects.filter(pk=self.article.pk).exists())

    def test_other_journalist_cannot_delete_article(self):
        """Verify that other journalist cannot delete article."""
        self.client.force_login(self.other_journalist)

        response = self.client.post(f"/articles/{self.article.pk}/delete/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Article.objects.filter(pk=self.article.pk).exists())

    def test_anonymous_user_cannot_delete_article(self):
        """Verify that anonymous user cannot delete article."""
        response = self.client.post(f"/articles/{self.article.pk}/delete/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        self.assertTrue(Article.objects.filter(pk=self.article.pk).exists())


class NewsletterPageTests(TestCase):
    """Check newsletter creation permissions and article visibility."""

    @classmethod
    def setUpTestData(cls):
        """Create shared accounts and content for this test class."""
        configure_role_groups()

        cls.journalist = User.objects.create_user(
            username="newsletter_writer",
            role=User.Role.JOURNALIST,
        )
        cls.reader = User.objects.create_user(
            username="newsletter_reader",
            role=User.Role.READER,
        )
        cls.editor = User.objects.create_user(
            username="newsletter_editor",
            role=User.Role.EDITOR,
        )

        cls.published = Article.objects.create(
            title="TOKOLOSHE DEMANDS ITS OWN PARKING BAY!",
            content="A fictional parking dispute.",
            author=cls.journalist,
            approved=True,
        )
        cls.draft = Article.objects.create(
            title="SECRET TOKOLOSHE DRAFT",
            content="This story has not been approved.",
            author=cls.journalist,
        )

    def test_journalist_can_create_newsletter(self):
        """Verify that journalist can create newsletter."""
        self.client.force_login(self.journalist)

        response = self.client.post(
            "/newsletters/create/",
            {
                "title": "Tokoloshe Weekly",
                "description": "Fictional supernatural news.",
                "publisher": "",
                "articles": [self.published.pk],
            },
        )

        newsletter = Newsletter.objects.get(title="Tokoloshe Weekly")

        self.assertRedirects(
            response,
            f"/newsletters/{newsletter.pk}/",
        )
        self.assertEqual(newsletter.author, self.journalist)
        self.assertEqual(
            list(newsletter.articles.all()),
            [self.published],
        )

    def test_reader_cannot_create_newsletters(self):
        """Verify that reader cannot create newsletters."""
        for user in (self.reader,):
            with self.subTest(role=user.role):
                self.client.force_login(user)

                response = self.client.post(
                    "/newsletters/create/",
                    {
                        "title": "Blocked newsletter",
                        "description": "This must not be created.",
                        "publisher": "",
                        "articles": [self.published.pk],
                    },
                )

                self.assertEqual(response.status_code, 403)

        self.assertFalse(
            Newsletter.objects.filter(
                title="Blocked newsletter",
            ).exists()
        )

    def test_editor_can_create_publisher_newsletter(self):
        """Editors can curate for their publisher without article-add rights."""
        publisher = Publisher.objects.create(name="Editorial Daily")
        publisher.editors.add(self.editor)
        self.client.force_login(self.editor)
        response = self.client.post(
            "/newsletters/create/",
            {
                "title": "Editor roundup",
                "description": "Approved stories",
                "publisher": publisher.pk,
                "articles": [self.published.pk],
            },
        )
        newsletter = Newsletter.objects.get(title="Editor roundup")
        self.assertRedirects(response, f"/newsletters/{newsletter.pk}/")
        self.assertEqual(newsletter.author, self.editor)
        self.assertEqual(newsletter.publisher, publisher)
        self.assertTrue(self.editor.has_perm("news.add_newsletter"))
        self.assertFalse(self.editor.has_perm("news.add_article"))

    def test_editor_cannot_choose_unaffiliated_publisher(self):
        """Reject a publisher outside the newsletter author's memberships."""
        publisher = Publisher.objects.create(name="Unrelated Editorial Daily")
        self.client.force_login(self.editor)
        response = self.client.post(
            "/newsletters/create/",
            {
                "title": "Invalid editor roundup",
                "description": "Stories",
                "publisher": publisher.pk,
                "articles": [self.published.pk],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("publisher", response.context["form"].errors)
        self.assertFalse(
            Newsletter.objects.filter(title="Invalid editor roundup").exists()
        )

    def test_draft_cannot_be_selected_for_newsletter(self):
        """Verify that draft cannot be selected for newsletter."""
        self.client.force_login(self.journalist)

        response = self.client.post(
            "/newsletters/create/",
            {
                "title": "Invalid newsletter",
                "description": "Contains an unpublished selection.",
                "publisher": "",
                "articles": [self.draft.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("articles", response.context["form"].errors)
        self.assertFalse(
            Newsletter.objects.filter(
                title="Invalid newsletter",
            ).exists()
        )

    def test_reader_sees_only_currently_approved_articles(self):
        """Verify that reader sees only currently approved articles."""
        newsletter = Newsletter.objects.create(
            title="Reader roundup",
            author=self.journalist,
        )
        newsletter.articles.add(self.published, self.draft)

        self.client.force_login(self.reader)
        response = self.client.get(f"/newsletters/{newsletter.pk}/")

        self.assertContains(response, self.published.title)
        self.assertNotContains(response, self.draft.title)

        # A previously published article can return to draft after editing.
        self.published.approved = False
        self.published.save()

        response = self.client.get(f"/newsletters/{newsletter.pk}/")
        self.assertNotContains(response, self.published.title)


class NewsletterManagementTests(TestCase):
    """Check newsletter editing, deletion and ownership restrictions."""

    @classmethod
    def setUpTestData(cls):
        """Create shared accounts and content for this test class."""
        configure_role_groups()

        cls.journalist = User.objects.create_user(
            username="newsletter_owner",
            role=User.Role.JOURNALIST,
        )
        cls.other_journalist = User.objects.create_user(
            username="newsletter_outsider",
            role=User.Role.JOURNALIST,
        )
        cls.editor = User.objects.create_user(
            username="newsletter_manager",
            role=User.Role.EDITOR,
        )
        cls.reader = User.objects.create_user(
            username="newsletter_viewer",
            role=User.Role.READER,
        )

        cls.article = Article.objects.create(
            title="TOKOLOSHE OPENS A CUSTOMER COMPLAINTS DESK!",
            content="A fictional supernatural customer service story.",
            author=cls.journalist,
            approved=True,
        )
        cls.newsletter = Newsletter.objects.create(
            title="Tokoloshe Weekly",
            description="Fictional news roundup.",
            author=cls.journalist,
        )
        cls.newsletter.articles.add(cls.article)

    def edit_data(self):
        """Return valid form data for an updated newsletter."""
        return {
            "title": "Tokoloshe Weekly — Updated",
            "description": "Now with even more fictional drama.",
            "publisher": "",
            "articles": [self.article.pk],
        }

    def test_author_can_edit_newsletter(self):
        """Verify that author can edit newsletter."""
        self.client.force_login(self.journalist)

        response = self.client.post(
            f"/newsletters/{self.newsletter.pk}/edit/",
            self.edit_data(),
        )

        self.assertRedirects(
            response,
            f"/newsletters/{self.newsletter.pk}/",
        )
        self.newsletter.refresh_from_db()
        self.assertEqual(
            self.newsletter.title,
            "Tokoloshe Weekly — Updated",
        )
        self.assertEqual(self.newsletter.author, self.journalist)

    def test_editor_can_edit_newsletter(self):
        """Verify that editor can edit newsletter."""
        self.client.force_login(self.editor)

        response = self.client.post(
            f"/newsletters/{self.newsletter.pk}/edit/",
            self.edit_data(),
        )

        self.assertEqual(response.status_code, 302)
        self.newsletter.refresh_from_db()
        self.assertEqual(
            self.newsletter.title,
            "Tokoloshe Weekly — Updated",
        )
        self.assertEqual(self.newsletter.author, self.journalist)

    def test_reader_and_other_journalist_cannot_edit(self):
        """Verify that reader and other journalist cannot edit."""
        for user in (self.reader, self.other_journalist):
            with self.subTest(role=user.role):
                self.client.force_login(user)

                response = self.client.post(
                    f"/newsletters/{self.newsletter.pk}/edit/",
                    self.edit_data(),
                )

                self.assertEqual(response.status_code, 403)

        self.newsletter.refresh_from_db()
        self.assertEqual(self.newsletter.title, "Tokoloshe Weekly")

    def test_confirmation_page_preserves_newsletter(self):
        """Verify that confirmation page preserves newsletter."""
        self.client.force_login(self.journalist)

        response = self.client.get(
            f"/newsletters/{self.newsletter.pk}/delete/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "news/newsletter_confirm_delete.html",
        )
        self.assertTrue(
            Newsletter.objects.filter(pk=self.newsletter.pk).exists()
        )

    def test_author_can_delete_collection_without_deleting_articles(self):
        """Verify that author can delete collection without deleting articles."""
        self.client.force_login(self.journalist)

        response = self.client.post(
            f"/newsletters/{self.newsletter.pk}/delete/"
        )

        self.assertRedirects(response, "/newsletters/")
        self.assertFalse(
            Newsletter.objects.filter(pk=self.newsletter.pk).exists()
        )
        self.assertTrue(Article.objects.filter(pk=self.article.pk).exists())

    def test_editor_can_delete_newsletter(self):
        """Verify that editor can delete newsletter."""
        self.client.force_login(self.editor)

        response = self.client.post(
            f"/newsletters/{self.newsletter.pk}/delete/"
        )

        self.assertRedirects(response, "/newsletters/")
        self.assertFalse(
            Newsletter.objects.filter(pk=self.newsletter.pk).exists()
        )

    def test_reader_and_other_journalist_cannot_delete(self):
        """Verify that reader and other journalist cannot delete."""
        for user in (self.reader, self.other_journalist):
            with self.subTest(role=user.role):
                self.client.force_login(user)

                response = self.client.post(
                    f"/newsletters/{self.newsletter.pk}/delete/"
                )

                self.assertEqual(response.status_code, 403)

        self.assertTrue(
            Newsletter.objects.filter(pk=self.newsletter.pk).exists()
        )


class SubscriptionManagementTests(TestCase):
    """Check reader subscription changes and account isolation."""

    @classmethod
    def setUpTestData(cls):
        """Create shared accounts and content for this test class."""
        configure_role_groups()

        cls.reader = User.objects.create_user(
            username="subscription_reader",
            role=User.Role.READER,
        )
        cls.other_reader = User.objects.create_user(
            username="other_subscription_reader",
            role=User.Role.READER,
        )
        cls.journalist = User.objects.create_user(
            username="subscription_journalist",
            role=User.Role.JOURNALIST,
        )
        cls.editor = User.objects.create_user(
            username="subscription_editor",
            role=User.Role.EDITOR,
        )
        cls.publisher = Publisher.objects.create(
            name="Tokoloshe Times",
        )

    def test_reader_can_open_subscription_page(self):
        """Verify that reader can open subscription page."""
        self.client.force_login(self.reader)

        response = self.client.get("/subscriptions/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "news/subscription_form.html",
        )

    def test_reader_can_subscribe_without_changing_another_account(self):
        """Verify that reader can subscribe without changing another account."""
        self.client.force_login(self.reader)

        response = self.client.post(
            "/subscriptions/",
            {
                "subscribed_publishers": [self.publisher.pk],
                "subscribed_journalists": [self.journalist.pk],
                # Extra data must not choose the account being updated.
                "id": self.other_reader.pk,
            },
        )

        self.assertRedirects(response, "/subscriptions/")
        self.assertTrue(
            self.reader.subscribed_publishers.filter(
                pk=self.publisher.pk,
            ).exists()
        )
        self.assertTrue(
            self.reader.subscribed_journalists.filter(
                pk=self.journalist.pk,
            ).exists()
        )
        self.assertFalse(self.other_reader.subscribed_publishers.exists())
        self.assertFalse(self.other_reader.subscribed_journalists.exists())

    def test_reader_can_unsubscribe_from_everyone(self):
        """Verify that reader can unsubscribe from everyone."""
        self.reader.subscribed_publishers.add(self.publisher)
        self.reader.subscribed_journalists.add(self.journalist)
        self.client.force_login(self.reader)

        response = self.client.post("/subscriptions/", {})

        self.assertRedirects(response, "/subscriptions/")
        self.assertFalse(self.reader.subscribed_publishers.exists())
        self.assertFalse(self.reader.subscribed_journalists.exists())

    def test_reader_cannot_subscribe_to_an_editor_as_a_journalist(self):
        """Verify that reader cannot subscribe to an editor as a journalist."""
        self.client.force_login(self.reader)

        response = self.client.post(
            "/subscriptions/",
            {
                "subscribed_publishers": [],
                "subscribed_journalists": [self.editor.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "subscribed_journalists",
            response.context["form"].errors,
        )
        self.assertFalse(self.reader.subscribed_journalists.exists())

    def test_editors_and_journalists_cannot_manage_reader_subscriptions(self):
        """Verify that editors and journalists cannot manage reader subscriptions."""
        for user in (self.editor, self.journalist):
            with self.subTest(role=user.role):
                self.client.force_login(user)

                response = self.client.post(
                    "/subscriptions/",
                    {"subscribed_publishers": [self.publisher.pk]},
                )

                self.assertEqual(response.status_code, 403)
                self.assertFalse(user.subscribed_publishers.exists())

    def test_anonymous_user_is_redirected_to_login(self):
        """Verify that anonymous user is redirected to login."""
        response = self.client.get("/subscriptions/")

        self.assertRedirects(
            response,
            "/accounts/login/?next=/subscriptions/",
        )


class ArticleReadAPITests(TestCase):
    """Verify real token authentication and subscription filtering."""

    @classmethod
    def setUpTestData(cls):
        """Create shared accounts and content for this test class."""
        configure_role_groups()

        cls.reader = User.objects.create_user(
            username="api_reader",
            password="ApiTestingPass!482",
            role=User.Role.READER,
        )
        cls.journalist = User.objects.create_user(
            username="api_journalist",
            role=User.Role.JOURNALIST,
        )
        cls.other_journalist = User.objects.create_user(
            username="other_api_journalist",
            role=User.Role.JOURNALIST,
        )

        cls.publisher = Publisher.objects.create(
            name="Tokoloshe Times",
        )
        cls.publisher.journalists.add(cls.journalist)

        cls.publisher_article = Article.objects.create(
            title="TOKOLOSHE ASKS FOR A SALARY INCREASE!",
            content="A fictional workplace dispute.",
            author=cls.journalist,
            publisher=cls.publisher,
            approved=True,
        )
        cls.independent_article = Article.objects.create(
            title="TOKOLOSHE STARTS WORKING FROM HOME!",
            content="A fictional independent story.",
            author=cls.journalist,
            approved=True,
        )
        cls.unrelated_article = Article.objects.create(
            title="Unrelated published story",
            content="Written by a different journalist.",
            author=cls.other_journalist,
            approved=True,
        )
        cls.draft = Article.objects.create(
            title="Private API draft",
            content="Not approved.",
            author=cls.journalist,
        )

        cls.token = Token.objects.create(user=cls.reader)

    def setUp(self):
        """Prepare isolated authentication or client state for each test."""
        self.api_client = APIClient()

    def authenticate_reader(self):
        """Use an actual token header rather than bypass authentication."""
        self.api_client.credentials(
            HTTP_AUTHORIZATION=f"Token {self.token.key}"
        )

    def test_login_returns_token_and_rejects_wrong_password(self):
        """Verify that login returns token and rejects wrong password."""
        response = self.api_client.post(
            "/api/token/",
            {
                "username": "api_reader",
                "password": "ApiTestingPass!482",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["token"], self.token.key)

        response = self.api_client.post(
            "/api/token/",
            {
                "username": "api_reader",
                "password": "WrongPassword",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_or_invalid_token_is_rejected(self):
        """Verify that missing or invalid token is rejected."""
        response = self.api_client.get("/api/articles/")
        self.assertEqual(response.status_code, 401)

        self.api_client.credentials(HTTP_AUTHORIZATION="Token invalid-token")
        response = self.api_client.get("/api/articles/")
        self.assertEqual(response.status_code, 401)

    def test_article_list_excludes_drafts(self):
        """Verify that article list excludes drafts."""
        self.authenticate_reader()

        response = self.api_client.get("/api/articles/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {article["id"] for article in response.data},
            {
                self.publisher_article.pk,
                self.independent_article.pk,
                self.unrelated_article.pk,
            },
        )

    def test_subscription_feed_matches_publisher_and_journalist(self):
        """Verify that subscription feed matches publisher and journalist."""
        self.authenticate_reader()

        # With no subscriptions, the feed should be empty.
        response = self.api_client.get("/api/articles/subscribed/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

        # A publisher subscription includes only that publisher's work.
        self.reader.subscribed_publishers.add(self.publisher)

        response = self.api_client.get("/api/articles/subscribed/")
        self.assertEqual(
            [article["id"] for article in response.data],
            [self.publisher_article.pk],
        )

        # Following the author adds independent work without duplicates.
        self.reader.subscribed_journalists.add(self.journalist)

        response = self.api_client.get("/api/articles/subscribed/")
        self.assertEqual(
            {article["id"] for article in response.data},
            {
                self.publisher_article.pk,
                self.independent_article.pk,
            },
        )
        self.assertEqual(len(response.data), 2)

    def test_detail_returns_published_article_and_hides_draft(self):
        """Verify that detail returns published article and hides draft."""
        self.authenticate_reader()

        response = self.api_client.get(
            f"/api/articles/{self.publisher_article.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["id"],
            self.publisher_article.pk,
        )

        response = self.api_client.get(f"/api/articles/{self.draft.pk}/")
        self.assertEqual(response.status_code, 404)


class ArticleWriteAPITests(TestCase):
    """Verify token-authenticated writes and publication permissions."""

    @classmethod
    def setUpTestData(cls):
        """Create shared accounts and content for this test class."""
        configure_role_groups()

        cls.journalist = User.objects.create_user(
            username="api_writer",
            role=User.Role.JOURNALIST,
        )
        cls.other_journalist = User.objects.create_user(
            username="other_api_writer",
            role=User.Role.JOURNALIST,
        )
        cls.editor = User.objects.create_user(
            username="api_editor",
            role=User.Role.EDITOR,
        )
        cls.reader = User.objects.create_user(
            username="api_viewer",
            role=User.Role.READER,
        )

        cls.article = Article.objects.create(
            title="TOKOLOSHE LAUNCHES A TECH STARTUP!",
            content="A fictional startup promises unlimited Wi-Fi.",
            author=cls.journalist,
            approved=True,
        )

    def setUp(self):
        """Prepare isolated authentication or client state for each test."""
        self.api_client = APIClient()

    def authenticate(self, user):
        """Authenticate requests using a real token."""
        token, _ = Token.objects.get_or_create(user=user)
        self.api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def article_data(self):
        """Return valid content for an API submission."""
        return {
            "title": "TOKOLOSHE STARTUP RUNS OUT OF DATA!",
            "content": "Investors demand answers in this fictional story.",
            "publisher": None,
        }

    def test_journalist_can_create_without_spoofing_author_or_approval(self):
        """Verify that journalist can create without spoofing author or approval."""
        self.authenticate(self.journalist)
        data = self.article_data()
        data["author"] = self.other_journalist.pk
        data["approved"] = True

        response = self.api_client.post(
            "/api/articles/",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        article = Article.objects.get(pk=response.data["id"])
        self.assertEqual(article.author, self.journalist)
        self.assertFalse(article.approved)

    def test_reader_and_editor_cannot_create(self):
        """Verify that reader and editor cannot create."""
        for user in (self.reader, self.editor):
            with self.subTest(role=user.role):
                self.authenticate(user)

                response = self.api_client.post(
                    "/api/articles/",
                    self.article_data(),
                    format="json",
                )
                self.assertEqual(response.status_code, 403)

        self.assertEqual(Article.objects.count(), 1)

    def test_invalid_publisher_membership_is_rejected(self):
        """Verify that invalid publisher membership is rejected."""
        publisher = Publisher.objects.create(name="Other News")
        self.authenticate(self.journalist)
        data = self.article_data()
        data["publisher"] = publisher.pk

        response = self.api_client.post(
            "/api/articles/",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("publisher", response.data)
        self.assertEqual(Article.objects.count(), 1)

    def test_author_and_editor_can_update_with_reapproval(self):
        """Verify that author and editor can update with reapproval."""
        for user in (self.journalist, self.editor):
            with self.subTest(role=user.role):
                self.article.approved = True
                self.article.save()
                self.authenticate(user)

                response = self.api_client.put(
                    f"/api/articles/{self.article.pk}/",
                    self.article_data(),
                    format="json",
                )

                self.assertEqual(response.status_code, 200)
                self.article.refresh_from_db()
                self.assertFalse(self.article.approved)
                self.assertEqual(
                    self.article.author,
                    self.journalist,
                )

    def test_reader_and_other_author_cannot_update_or_delete(self):
        """Verify that reader and other author cannot update or delete."""
        for user in (self.reader, self.other_journalist):
            with self.subTest(role=user.role):
                self.authenticate(user)
                url = f"/api/articles/{self.article.pk}/"

                response = self.api_client.put(
                    url,
                    self.article_data(),
                    format="json",
                )
                self.assertEqual(response.status_code, 403)

                response = self.api_client.delete(url)
                self.assertEqual(response.status_code, 403)

        self.article.refresh_from_db()
        self.assertEqual(
            self.article.title,
            "TOKOLOSHE LAUNCHES A TECH STARTUP!",
        )

    def test_author_and_editor_can_delete(self):
        """Verify that author and editor can delete."""
        for user in (self.journalist, self.editor):
            with self.subTest(role=user.role):
                article = Article.objects.create(
                    title="Disposable fictional article",
                    content="For deletion testing.",
                    author=self.journalist,
                )
                self.authenticate(user)

                response = self.api_client.delete(
                    f"/api/articles/{article.pk}/"
                )

                self.assertEqual(response.status_code, 204)
                self.assertFalse(
                    Article.objects.filter(pk=article.pk).exists()
                )

    @patch("news.api_views.notify_article_approval", return_value=[])
    def test_editor_approval_notifies_only_once(self, mock_notify):
        """Verify that editor approval notifies only once."""
        self.article.approved = False
        self.article.save()
        self.authenticate(self.editor)

        url = f"/api/articles/{self.article.pk}/approve/"

        response = self.api_client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["newly_approved"])

        self.article.refresh_from_db()
        self.assertTrue(self.article.approved)
        mock_notify.assert_called_once()

        response = self.api_client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["newly_approved"])
        mock_notify.assert_called_once()

    def test_reader_and_journalist_cannot_approve(self):
        """Verify that reader and journalist cannot approve."""
        self.article.approved = False
        self.article.save()

        for user in (self.reader, self.journalist):
            with self.subTest(role=user.role):
                self.authenticate(user)

                response = self.api_client.post(
                    f"/api/articles/{self.article.pk}/approve/"
                )
                self.assertEqual(response.status_code, 403)

        self.article.refresh_from_db()
        self.assertFalse(self.article.approved)


class PublicationFailureTests(TestCase):
    """Keep published content available when delivery fails independently."""

    @classmethod
    def setUpTestData(cls):
        """Create a draft, an editor and an active subscribed reader."""
        configure_role_groups()
        cls.writer = User.objects.create_user(
            username="failure_writer", role=User.Role.JOURNALIST
        )
        cls.editor = User.objects.create_user(
            username="failure_editor", role=User.Role.EDITOR
        )
        cls.reader = User.objects.create_user(
            username="failure_reader", email="reader@example.com"
        )
        cls.reader.subscribed_journalists.add(cls.writer)
        cls.article = Article.objects.create(
            title="Delivery test", content="Fictional story", author=cls.writer
        )

    @patch("news.notifications.requests.post")
    @patch(
        "news.notifications.send_mass_mail",
        side_effect=OSError("mail unavailable"),
    )
    def test_email_failure_still_posts_and_preserves_approval(
        self, send_mail, post
    ):
        """Email failure is reported without preventing the internal event."""
        post.return_value.status_code = 201
        self.client.force_login(self.editor)
        with self.assertLogs("news.notifications", level="ERROR"):
            response = self.client.post(
                f"/articles/{self.article.pk}/approve/"
            )
        self.article.refresh_from_db()
        self.assertTrue(self.article.approved)
        self.assertEqual(response.status_code, 302)
        post.assert_called_once()
        send_mail.assert_called_once()

    @patch("news.notifications.requests.post")
    @patch("news.notifications.send_mass_mail")
    def test_http_failure_returns_warning_after_email(self, send_mail, post):
        """The API caller receives a delivery warning while approval persists."""
        import requests

        post.side_effect = requests.Timeout("unreachable")
        client = APIClient()
        token = Token.objects.create(user=self.editor)
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        with self.assertLogs("news.notifications", level="ERROR"):
            response = client.post(f"/api/articles/{self.article.pk}/approve/")
        self.article.refresh_from_db()
        self.assertTrue(self.article.approved)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["warnings"],
            ["The internal API notification failed."],
        )
        send_mail.assert_called_once()

    def test_admin_content_edit_requires_reapproval(self):
        """Administrative edits cannot leave changed content published."""
        from django.contrib.admin import AdminSite
        from django.test import RequestFactory
        from .admin import ArticleAdmin

        self.article.approved = True
        self.article.save()
        self.article.content = "Changed story"
        request = RequestFactory().post("/admin/")
        request.user = self.editor
        article_admin = ArticleAdmin(Article, AdminSite())
        article_admin.save_model(
            request, self.article, Mock(changed_data=["content"]), True
        )
        self.article.refresh_from_db()
        self.assertFalse(self.article.approved)
        self.assertIn(
            "approved",
            article_admin.get_readonly_fields(request, self.article),
        )


class RegistrationWorkflowTests(TestCase):
    """Exercise public registration, password validation and immediate access."""

    password = "Garden!9River-Sunshine"

    def registration_data(self, username="new_reader", role=User.Role.READER):
        """Return valid public sign-up fields without privileged attributes."""
        return {
            "username": username,
            "email": f"{username}@example.com",
            "role": role,
            "password1": self.password,
            "password2": self.password,
        }

    def test_login_and_registration_pages_are_linked(self):
        """New visitors can discover sign-up and return to the login page."""
        response = self.client.get("/accounts/login/")
        self.assertContains(response, 'href="/accounts/register/"')
        response = self.client.get("/accounts/register/")
        self.assertContains(response, 'href="/accounts/login/"')
        self.assertContains(response, 'name="role"')

    def test_every_role_gets_group_permissions_on_registration(self):
        """Registration works without pre-created groups or demo accounts."""
        # Start with no role groups, as on a fresh reviewer installation.
        Group.objects.filter(
            name__in=["Reader", "Editor", "Journalist"]
        ).delete()
        for role, group in [
            (User.Role.READER, "Reader"),
            (User.Role.JOURNALIST, "Journalist"),
            (User.Role.EDITOR, "Editor"),
        ]:
            with self.subTest(role=role):
                self.client.logout()
                username = f"registered_{role}"
                response = self.client.post(
                    "/accounts/register/",
                    self.registration_data(username, role),
                )
                self.assertRedirects(response, "/")
                user = User.objects.get(username=username)
                self.assertTrue(user.check_password(self.password))
                self.assertEqual(
                    list(user.groups.values_list("name", flat=True)), [group]
                )
                self.assertTrue(user.has_perm("news.view_article"))
                self.assertEqual(
                    user.has_perm("news.approve_article"),
                    role == User.Role.EDITOR,
                )
                self.assertEqual(
                    user.has_perm("news.add_article"),
                    role == User.Role.JOURNALIST,
                )
                self.assertEqual(
                    self.client.session["_auth_user_id"], str(user.pk)
                )
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)

    def test_signup_does_not_accept_staff_or_superuser_flags(self):
        """Forged public form fields cannot create administrative accounts."""
        data = self.registration_data()
        data.update(is_staff="true", is_superuser="true", groups=[1])
        self.client.post("/accounts/register/", data)
        user = User.objects.get(username=data["username"])
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_perm("news.approve_article"))

    def test_invalid_role_is_rejected(self):
        """Only the three news roles can be selected."""
        response = self.client.post(
            "/accounts/register/", self.registration_data(role="publisher")
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("role", response.context["form"].errors)
        self.assertFalse(User.objects.exists())

    def test_invalid_email_is_rejected(self):
        """Require a usable email field for subscriber notifications."""
        data = self.registration_data()
        data["email"] = "invalid"
        response = self.client.post("/accounts/register/", data)
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(User.objects.exists())

    def test_password_mismatch_is_rejected(self):
        """Reject mismatched confirmation without saving an account."""
        data = self.registration_data()
        data["password2"] = "different"
        response = self.client.post("/accounts/register/", data)
        self.assertIn("password2", response.context["form"].errors)
        self.assertFalse(User.objects.exists())

    def test_weak_password_is_rejected(self):
        """Use Django's password validators rather than storing weak choices."""
        data = self.registration_data()
        data.update(password1="123", password2="123")
        response = self.client.post("/accounts/register/", data)
        self.assertIn("password2", response.context["form"].errors)
        self.assertFalse(User.objects.exists())

    def test_duplicate_username_is_rejected(self):
        """Show a field error instead of overwriting an existing account."""
        User.objects.create_user(username="new_reader")
        response = self.client.post(
            "/accounts/register/", self.registration_data()
        )
        self.assertIn("username", response.context["form"].errors)
        self.assertEqual(User.objects.count(), 1)

    def test_authenticated_user_cannot_register_another_account(self):
        """An existing session returns to its dashboard without role changes."""
        configure_role_groups()
        user = User.objects.create_user(username="existing")
        self.client.force_login(user)
        self.assertRedirects(self.client.get("/accounts/register/"), "/")
        self.client.post("/accounts/register/", self.registration_data())
        self.assertEqual(User.objects.count(), 1)

    def test_registration_requires_csrf(self):
        """Cross-site submissions cannot create browser accounts."""
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        response = client.post("/accounts/register/", self.registration_data())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.exists())


class PublisherWorkflowTests(TestCase):
    """Verify newsroom setup and affiliation boundaries without admin access."""

    @classmethod
    def setUpTestData(cls):
        """Create role accounts and one affiliated publication."""
        configure_role_groups()
        cls.editor = User.objects.create_user(
            username="publisher_editor", role=User.Role.EDITOR
        )
        cls.other_editor = User.objects.create_user(
            username="unaffiliated_editor", role=User.Role.EDITOR
        )
        cls.writer = User.objects.create_user(
            username="publisher_writer", role=User.Role.JOURNALIST
        )
        cls.reader = User.objects.create_user(username="publisher_reader")
        cls.inactive_writer = User.objects.create_user(
            username="inactive_writer",
            role=User.Role.JOURNALIST,
            is_active=False,
        )
        cls.publisher = Publisher.objects.create(name="Existing Daily")
        cls.publisher.editors.add(cls.editor)

    def payload(self, **overrides):
        """Provide valid newsroom selections for create and update requests."""
        data = {
            "name": "New Daily",
            "description": "Local news",
            "journalists": [self.writer.pk],
            "editors": [],
        }
        data.update(overrides)
        return data

    def test_editor_creates_publisher_and_keeps_management_access(self):
        """Save a publication and retain its creator even if no editor is selected."""
        self.client.force_login(self.editor)
        response = self.client.post("/publishers/create/", self.payload())
        publisher = Publisher.objects.get(name="New Daily")
        self.assertRedirects(response, f"/publishers/{publisher.pk}/")
        self.assertTrue(publisher.editors.filter(pk=self.editor.pk).exists())
        self.assertTrue(
            publisher.journalists.filter(pk=self.writer.pk).exists()
        )
        self.assertFalse(self.editor.is_staff)

    def test_only_editors_can_create_publishers(self):
        """Readers and journalists cannot submit hidden publication forms."""
        for user in (self.reader, self.writer):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get("/publishers/create/").status_code, 403
                )
                self.assertEqual(
                    self.client.post(
                        "/publishers/create/", self.payload()
                    ).status_code,
                    403,
                )
        self.assertFalse(Publisher.objects.filter(name="New Daily").exists())

    def test_unaffiliated_editor_cannot_edit_publisher(self):
        """Direct GET and POST URLs enforce publication membership."""
        self.client.force_login(self.other_editor)
        url = f"/publishers/{self.publisher.pk}/edit/"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(url, self.payload()).status_code, 403
        )
        self.publisher.refresh_from_db()
        self.assertEqual(self.publisher.name, "Existing Daily")

    def test_affiliated_editor_can_update_details_and_members(self):
        """A manager can assign another editor and journalists through the site."""
        self.client.force_login(self.editor)
        response = self.client.post(
            f"/publishers/{self.publisher.pk}/edit/",
            self.payload(editors=[self.other_editor.pk]),
        )
        self.assertRedirects(response, f"/publishers/{self.publisher.pk}/")
        self.publisher.refresh_from_db()
        self.assertEqual(self.publisher.name, "New Daily")
        self.assertTrue(
            self.publisher.editors.filter(pk=self.other_editor.pk).exists()
        )
        self.assertTrue(
            self.publisher.editors.filter(pk=self.editor.pk).exists()
        )
        self.client.force_login(self.other_editor)
        self.assertEqual(
            self.client.get(
                f"/publishers/{self.publisher.pk}/edit/"
            ).status_code,
            200,
        )

    def test_members_cannot_have_the_wrong_role(self):
        """Reject role-confused membership IDs on both affiliation fields."""
        self.client.force_login(self.editor)
        response = self.client.post(
            "/publishers/create/",
            self.payload(
                editors=[self.writer.pk], journalists=[self.reader.pk]
            ),
        )
        self.assertIn("editors", response.context["form"].errors)
        self.assertIn("journalists", response.context["form"].errors)
        self.assertFalse(Publisher.objects.filter(name="New Daily").exists())

    def test_inactive_journalist_cannot_be_assigned(self):
        """Inactive accounts are not selectable even through a crafted POST."""
        self.client.force_login(self.editor)
        response = self.client.post(
            "/publishers/create/",
            self.payload(journalists=[self.inactive_writer.pk]),
        )
        self.assertIn("journalists", response.context["form"].errors)

    def test_duplicate_publisher_name_is_a_form_error(self):
        """Respect the model's unique publication name without a server error."""
        self.client.force_login(self.editor)
        response = self.client.post(
            "/publishers/create/", self.payload(name="Existing Daily")
        )
        self.assertIn("name", response.context["form"].errors)
        self.assertEqual(Publisher.objects.count(), 1)

    def test_anonymous_users_are_redirected_for_all_publisher_pages(self):
        """Publication browsing and management require a news account."""
        for url in (
            "/publishers/",
            "/publishers/create/",
            f"/publishers/{self.publisher.pk}/",
            f"/publishers/{self.publisher.pk}/edit/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)

    def test_reader_publication_page_hides_drafts_and_management(self):
        """Publisher detail obeys the same draft privacy rule as article pages."""
        Article.objects.create(
            title="Hidden publisher draft",
            content="Draft",
            author=self.writer,
            publisher=self.publisher,
        )
        Article.objects.create(
            title="Published publisher story",
            content="Public",
            author=self.writer,
            publisher=self.publisher,
            approved=True,
        )
        self.client.force_login(self.reader)
        response = self.client.get(f"/publishers/{self.publisher.pk}/")
        self.assertContains(response, "Published publisher story")
        self.assertNotContains(response, "Hidden publisher draft")
        self.assertNotContains(response, "Manage publisher")
        self.assertContains(response, "Manage your subscriptions")

    def test_non_editors_cannot_manage_memberships(self):
        """Reader and journalist accounts cannot edit a publication directly."""
        for user in (self.reader, self.writer):
            self.client.force_login(user)
            response = self.client.post(
                f"/publishers/{self.publisher.pk}/edit/", self.payload()
            )
            self.assertEqual(response.status_code, 403)

    def test_removed_author_affiliation_is_reported_as_a_form_error(self):
        """A stale article affiliation must not produce an excluded-field error."""
        article = Article.objects.create(
            title="Prior affiliation",
            content="Story",
            author=self.writer,
            publisher=self.publisher,
        )
        # The member was removed after authoring; current form choices reject it.
        self.client.force_login(self.writer)
        response = self.client.post(
            f"/articles/{article.pk}/edit/",
            {
                "title": "Changed",
                "content": "Updated",
                "publisher": self.publisher.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("publisher", response.context["form"].errors)
        article.refresh_from_db()
        self.assertEqual(article.title, "Prior affiliation")

    def test_publisher_updates_require_csrf(self):
        """A forged browser request cannot alter newsroom memberships."""
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.editor)
        response = client.post(
            f"/publishers/{self.publisher.pk}/edit/", self.payload()
        )
        self.assertEqual(response.status_code, 403)


class StandaloneNewsJourneyTests(TestCase):
    """Exercise a complete fresh-install journey using public web forms only."""

    @patch("news.views.notify_article_approval", return_value=[])
    def test_register_publish_curate_and_subscribe_without_admin(self, notify):
        """All roles can complete the main workflow without staff credentials."""
        password = "Garden!9River-Sunshine"
        for username, role in [
            ("journey_writer", "journalist"),
            ("journey_reader", "reader"),
            ("journey_editor", "editor"),
        ]:
            self.client.logout()
            response = self.client.post(
                "/accounts/register/",
                {
                    "username": username,
                    "email": f"{username}@example.com",
                    "role": role,
                    "password1": password,
                    "password2": password,
                },
            )
            self.assertRedirects(response, "/")
        writer = User.objects.get(username="journey_writer")
        reader = User.objects.get(username="journey_reader")
        editor = User.objects.get(username="journey_editor")
        self.client.post(
            "/publishers/create/",
            {
                "name": "Journey Daily",
                "description": "Our newsroom",
                "journalists": [writer.pk],
            },
        )
        publisher = Publisher.objects.get(name="Journey Daily")
        self.client.post("/accounts/logout/")
        self.assertTrue(
            self.client.login(username=writer.username, password=password)
        )
        self.assertContains(self.client.get("/"), "Create article")
        self.client.post(
            "/articles/create/",
            {
                "title": "TOKOLOSHE TAKES THE BUS!",
                "content": "A fictional commute.",
                "publisher": publisher.pk,
            },
        )
        article = Article.objects.get(title="TOKOLOSHE TAKES THE BUS!")
        self.assertFalse(article.approved)
        self.client.post("/accounts/logout/")
        self.assertTrue(
            self.client.login(username=reader.username, password=password)
        )
        self.assertEqual(
            self.client.get(f"/articles/{article.pk}/").status_code, 404
        )
        self.client.post(
            "/subscriptions/",
            {
                "subscribed_publishers": [publisher.pk],
                "subscribed_journalists": [writer.pk],
            },
        )
        self.client.post("/accounts/logout/")
        self.assertTrue(
            self.client.login(username=editor.username, password=password)
        )
        self.client.post(f"/articles/{article.pk}/approve/")
        notify.assert_called_once()
        article.refresh_from_db()
        self.assertTrue(article.approved)
        self.client.post(
            "/newsletters/create/",
            {
                "title": "Journey Weekly",
                "description": "A roundup",
                "publisher": publisher.pk,
                "articles": [article.pk],
            },
        )
        newsletter = Newsletter.objects.get(title="Journey Weekly")
        self.client.post("/accounts/logout/")
        self.assertTrue(
            self.client.login(username=reader.username, password=password)
        )
        self.assertContains(
            self.client.get(f"/articles/{article.pk}/"), "A fictional commute."
        )
        self.assertContains(
            self.client.get(f"/newsletters/{newsletter.pk}/"), article.title
        )
        self.assertTrue(
            reader.subscribed_publishers.filter(pk=publisher.pk).exists()
        )
        for user in (writer, reader, editor):
            self.assertFalse(user.is_staff)
            self.assertFalse(user.is_superuser)
