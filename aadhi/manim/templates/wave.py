"""wave: a sinusoid whose amplitude / frequency / phase change step by step."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ._base import ColorName, Finite, NoteText, SceneTemplate, TemplateParams, TitleText

MAX_CYCLES = 30


class WaveStep(TemplateParams):
    amplitude: Finite = Field(ge=0, le=100)
    frequency: Finite = Field(ge=0, le=1000, description="cycles per unit of x (Hz when x is time in s)")
    phase_deg: Finite = Field(default=0.0, ge=-360, le=360)
    keep_previous: bool = Field(default=False, description="leave a faint copy of the previous wave to compare")
    note: NoteText = ""


class WaveParams(TemplateParams):
    title: TitleText = ""
    function: Literal["sin", "cos"] = "sin"
    x_max: Finite = Field(default=2.0, gt=0, le=1000, description="end of the x axis (x starts at 0)")
    x_label: str = Field(default="t (s)", max_length=12)
    y_label: str = Field(default="y", max_length=12)
    unit_frequency: str = Field(default="Hz", max_length=8)
    color: ColorName = "cyan"
    show_readout: bool = Field(default=True, description="show A, f and phase values")
    steps: list[WaveStep] = Field(min_length=1, max_length=8, description="wave parameters at each beat")

    @model_validator(mode="after")
    def _semantics(self) -> WaveParams:
        for i, step in enumerate(self.steps):
            if step.frequency * self.x_max > MAX_CYCLES:
                raise ValueError(f"steps[{i}] shows {step.frequency * self.x_max:g} cycles; keep frequency*x_max <= {MAX_CYCLES}")
        if max(s.amplitude for s in self.steps) <= 0:
            raise ValueError("at least one step needs an amplitude > 0")
        return self


class WaveTemplate(SceneTemplate):
    name = "wave"
    title = "Sinusoidal wave"
    description = (
        "A sine/cosine wave y = A sin(2*pi*f*x + phase) on axes; each beat changes amplitude, frequency or phase "
        "with a smooth morph (optionally keeping a faint copy of the previous wave) and live A/f/phase readouts. "
        "Use for AC signals, sound, light, SHM, modulation, phase difference."
    )
    steps_hint = "one step per item of `steps`; step 0 draws the wave, later steps morph it"
    params_model = WaveParams
    scene_file = "wave.py"
    example_params = {
        "title": "Changing a sound wave",
        "function": "sin",
        "x_max": 2,
        "x_label": "t (s)",
        "y_label": "y",
        "color": "cyan",
        "show_readout": True,
        "steps": [
            {"amplitude": 1, "frequency": 1, "phase_deg": 0, "note": "A pure tone: one cycle per second"},
            {"amplitude": 2, "frequency": 1, "phase_deg": 0, "keep_previous": True, "note": "Double the amplitude: louder"},
            {"amplitude": 2, "frequency": 2, "phase_deg": 0, "note": "Double the frequency: higher pitch"},
            {"amplitude": 2, "frequency": 2, "phase_deg": 90, "keep_previous": True, "note": "A 90 degree phase shift"},
        ],
    }

    def step_count(self, params: BaseModel) -> int:
        return len(self.coerce(params).steps)

    def scene_data(self, params: WaveParams, *, target: str, language: str) -> dict[str, Any]:  # type: ignore[override]
        from .function_plot import nice_step

        data = params.model_dump(mode="json")
        a_max = max(s.amplitude for s in params.steps)
        data["y_max"] = round(a_max * 1.25, 5)
        data["y_step"] = nice_step(a_max * 2.5, 4)
        data["x_step"] = nice_step(params.x_max, 8)
        return data
