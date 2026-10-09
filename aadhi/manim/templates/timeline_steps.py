"""timeline_steps: events or process stages on a timeline, one per beat."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ._base import ColorName, SceneTemplate, TemplateParams, TitleText


class TimelineEvent(TemplateParams):
    marker: str = Field(min_length=1, max_length=16, description="short tag on the line, e.g. 1905 or Step 1")
    title: str = Field(min_length=1, max_length=40)
    detail: str = Field(default="", max_length=100, description="one short sentence")
    color: ColorName = "gold"


class TimelineStepsParams(TemplateParams):
    title: TitleText = ""
    events: list[TimelineEvent] = Field(min_length=1, max_length=8, description="in order; one appears per beat")


class TimelineStepsTemplate(SceneTemplate):
    name = "timeline_steps"
    title = "Timeline / process steps"
    description = (
        "A timeline (horizontal; vertical in side panels or with 7+ events) where each beat adds the next event or stage with a "
        "marker, title and one-line detail. Use for history of a discovery, life cycles, algorithm phases, "
        "manufacturing processes, project stages."
    )
    steps_hint = "one step per item of `events`"
    params_model = TimelineStepsParams
    scene_file = "timeline_steps.py"
    example_params = {
        "title": "History of the transistor",
        "events": [
            {"marker": "1947", "title": "Point-contact transistor", "detail": "Bell Labs replaces vacuum tubes", "color": "gold"},
            {"marker": "1954", "title": "Silicon transistor", "detail": "Works at higher temperatures", "color": "cyan"},
            {"marker": "1959", "title": "Integrated circuit", "detail": "Many transistors on one chip", "color": "pink"},
            {"marker": "1971", "title": "Microprocessor", "detail": "Intel 4004: a CPU on one chip", "color": "green"},
        ],
    }

    def step_count(self, params: BaseModel) -> int:
        return len(self.coerce(params).events)
