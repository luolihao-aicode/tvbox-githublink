from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from .models import ChannelCategory, ChannelRule

_QUALITY_SUFFIX = re.compile(r"(?:超高清|高清|标清|uhd|fhd|hd|频道)$", re.IGNORECASE)
_SEPARATORS = re.compile(r"[\s\-_·]+")


@dataclass(frozen=True, slots=True)
class ChannelMatch:
    channel_id: str | None = None
    channel_name: str | None = None
    category: ChannelCategory | None = None
    minimum_height: int | None = None
    failure_code: str | None = None


def normalize_channel_name(name: str) -> str:
    value = unicodedata.normalize("NFKC", name).strip().casefold()
    previous = None
    while value != previous:
        previous = value
        value = _QUALITY_SUFFIX.sub("", value).strip()
    return _SEPARATORS.sub("", value)


def classify_channel(name: str, rules: Sequence[ChannelRule]) -> ChannelMatch:
    key = normalize_channel_name(name)
    matches: list[ChannelRule] = []
    for rule in rules:
        if any(
            normalize_channel_name(alias) == key
            for alias in (rule.name, *rule.aliases)
        ):
            matches.append(rule)
    unique = {rule.id: rule for rule in matches}
    if not unique:
        return ChannelMatch(failure_code="UNKNOWN_CHANNEL")
    if len(unique) != 1:
        return ChannelMatch(failure_code="AMBIGUOUS_CHANNEL")
    rule = next(iter(unique.values()))
    return ChannelMatch(
        channel_id=rule.id,
        channel_name=rule.name,
        category=rule.category,
        minimum_height=rule.minimum_height,
    )
