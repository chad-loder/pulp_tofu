"""
Check `Plugin Writer's Guide`_ for more details.

.. _Plugin Writer's Guide:
    https://pulpproject.org/pulpcore/docs/dev/
"""

from logging import getLogger

from django.db import models

from pulpcore.plugin.models import (
    Content,
    ContentArtifact,
    Distribution,
    Publication,
    Remote,
    Repository,
)
from pulpcore.plugin.util import get_domain_pk

logger = getLogger(__name__)


class TofuContent(Content):
    """
    The "tofu" content type.

    Define fields you need for your new content type and
    specify uniqueness constraint to identify unit of this type.

    For example::

        field1 = models.TextField()
        field2 = models.IntegerField()
        field3 = models.CharField()

        class Meta:
            default_related_name = "%(app_label)s_%(model_name)s"
            unique_together = ("field1", "field2")
    """

    TYPE = "tofu"

    name = models.CharField(blank=False, null=False)
    _pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.PROTECT)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        unique_together = ("name", "_pulp_domain")


class TofuPublication(Publication):
    """
    A Publication for TofuContent.

    Define any additional fields for your new publication if needed.
    """

    TYPE = "tofu"

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"


class TofuRemote(Remote):
    """
    A Remote for TofuContent.

    Define any additional fields for your new remote if needed.
    """

    TYPE = "tofu"

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"


class TofuRepository(Repository):
    """
    A Repository for TofuContent.

    Define any additional fields for your new repository if needed.
    """

    TYPE = "tofu"

    CONTENT_TYPES = [TofuContent]

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"


class TofuDistribution(Distribution):
    """
    A Distribution for TofuContent.

    Define any additional fields for your new distribution if needed.
    """

    TYPE = "tofu"

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
