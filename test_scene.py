from manim import *
class TestScene(Scene):
    def construct(self):
        eq = MathTex(r"E=mc^2")
        self.play(Write(eq))
