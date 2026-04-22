from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Entity types — what Graphiti extracts from conversation as nodes
# ---------------------------------------------------------------------------

class UserPreference(BaseModel):
    """Something the user likes, dislikes, or has an opinion about."""

    category: Optional[str] = Field(
        None, description="Category: food, music, activity, topic, technology, etc."
    )
    sentiment: Optional[str] = Field(
        None, description="positive, negative, or neutral"
    )


class UserGoal(BaseModel):
    """A goal, aspiration, or thing the user wants to achieve."""

    timeframe: Optional[str] = Field(
        None, description="short-term, long-term, or ongoing"
    )
    status: Optional[str] = Field(
        None, description="active, completed, or abandoned"
    )


class UserHabit(BaseModel):
    """A recurring behavior or routine the user has or wants to build."""

    frequency: Optional[str] = Field(
        None, description="daily, weekly, occasional, etc."
    )
    sentiment: Optional[str] = Field(
        None, description="positive habit or habit to break"
    )


class Topic(BaseModel):
    """A subject, theme, or area the user discusses."""

    domain: Optional[str] = Field(
        None, description="work, personal, health, finance, learning, etc."
    )


class Person(BaseModel):
    """A person mentioned by the user."""

    relationship: Optional[str] = Field(
        None, description="friend, family, colleague, mentor, etc."
    )


# ---------------------------------------------------------------------------
# Type maps — passed to Graphiti for extraction
# Graphiti handles relationships with its default edge type; custom edge
# types are omitted because the Kuzu driver doesn't index them correctly.
# ---------------------------------------------------------------------------

ENTITY_TYPES = {
    "UserPreference": UserPreference,
    "UserGoal": UserGoal,
    "UserHabit": UserHabit,
    "Topic": Topic,
    "Person": Person,
}
