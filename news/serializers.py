"""Public representations and validation of writable article fields."""

from rest_framework import serializers

from .models import Article, Newsletter, Publisher, User


class UserSerializer(serializers.ModelSerializer):
    """Represent users without exposing passwords or private account data."""

    class Meta:
        """Declare model metadata or serializer/form field policy."""

        model = User
        fields = ("id", "username", "role")
        read_only_fields = fields


class PublisherSerializer(serializers.ModelSerializer):
    """Represent a publisher and its newsroom members."""

    class Meta:
        """Declare model metadata or serializer/form field policy."""

        model = Publisher
        fields = (
            "id",
            "name",
            "description",
            "editors",
            "journalists",
        )
        read_only_fields = fields


class ArticleSerializer(serializers.ModelSerializer):
    """Validate article content while protecting authorship and approval."""

    author_username = serializers.CharField(
        source="author.username",
        read_only=True,
    )
    publisher_name = serializers.CharField(
        source="publisher.name",
        read_only=True,
        default=None,
    )

    class Meta:
        """Declare model metadata or serializer/form field policy."""

        model = Article
        fields = (
            "id",
            "title",
            "content",
            "author",
            "author_username",
            "publisher",
            "publisher_name",
            "created_at",
            "updated_at",
            "approved",
        )
        read_only_fields = (
            "id",
            "author",
            "author_username",
            "publisher_name",
            "created_at",
            "updated_at",
            "approved",
        )

    def validate(self, attrs):
        """Check journalist authorship and selected publisher membership.

        Updates preserve the existing author; creates use the authenticated
        user. DRF does not call model.full_clean(), so this boundary repeats
        the model's membership rule before writing API data.
        """
        if self.instance is not None:
            author = self.instance.author
            current_publisher = self.instance.publisher
        else:
            author = self.context["request"].user
            current_publisher = None

        if author.role != User.Role.JOURNALIST:
            raise serializers.ValidationError(
                {"author": "The author must be a journalist."}
            )

        publisher = attrs.get("publisher", current_publisher)

        if publisher is not None:
            if not publisher.journalists.filter(pk=author.pk).exists():
                raise serializers.ValidationError(
                    {
                        "publisher": (
                            "The author does not belong to this publisher."
                        )
                    }
                )

        return attrs


class NewsletterSerializer(serializers.ModelSerializer):
    """Represent newsletters using only currently approved articles."""

    articles = serializers.SerializerMethodField()

    class Meta:
        """Declare model metadata or serializer/form field policy."""

        model = Newsletter
        fields = (
            "id",
            "title",
            "description",
            "author",
            "publisher",
            "created_at",
            "updated_at",
            "articles",
        )
        read_only_fields = fields

    def get_articles(self, newsletter):
        """Prevent a newsletter response from exposing article drafts."""
        articles = newsletter.articles.filter(
            approved=True,
        ).select_related("author", "publisher")

        return ArticleSerializer(articles, many=True).data
