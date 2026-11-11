# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import pytest

from benchbox.cli.commands.publish import publish_bundle
from benchbox.core.publishing.bundle_publisher import VALID_LABELS, BundlePublisher

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize("label", VALID_LABELS)
def test_valid_labels_are_accepted(tmp_path, label):
    publisher = BundlePublisher(destination=tmp_path, label=label)
    assert publisher.label == label


@pytest.mark.parametrize("bad", ["", "unofficial", "communtiy-submission", "MAINTAINER-RUN", "ci-verified"])
def test_invalid_label_raises_instead_of_coercing(tmp_path, bad):
    with pytest.raises(ValueError, match="Invalid trust label"):
        BundlePublisher(destination=tmp_path, label=bad)


def test_default_label_is_valid(tmp_path):
    assert BundlePublisher(destination=tmp_path).label == "maintainer-run"


def test_publish_bundle_rejects_invalid_label_without_traceback(tmp_path):
    result = publish_bundle(tmp_path / "does-not-exist.json", label="bogus", quiet=True)
    assert result is None


