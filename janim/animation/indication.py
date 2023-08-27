from __future__ import annotations
from typing import Callable

from janim.constants import *
from janim.items.item import Item, Point
from janim.items.vitem import VGroup
from janim.items.geometry.arc import Dot, Circle
from janim.items.geometry.line import Line
from janim.items.shape_matchers import SurroundingRectangle
from janim.animation.animation import Animation
from janim.animation.transform import Transform
from janim.animation.creation import ShowPartial, ShowCreation
from janim.animation.fading import FadeOut
from janim.animation.composition import AnimationGroup, Succession
from janim.utils.rate_functions import RateFunc, there_and_back
from janim.utils.bezier import interpolate

class FocusOn(Transform):
    def __init__(
        self, 
        point_or_item: np.ndarray | Item, 
        opacity: float = 0.2,
        color: JAnimColor = GREY,
        run_time: float = 2,
        **kwargs
    ) -> None:
        self.start_dot = Dot(
            radius=FRAME_X_RADIUS + FRAME_Y_RADIUS,
            fill_color=color,
            fill_opacity=0,
        )

        self.target_dot = Dot(radius=0)
        self.target_dot.set_fill(color, opacity)
        self.target_dot.add_updater(lambda d: d.move_to(point_or_item))

        super().__init__(self.start_dot, self.target_dot, run_time=run_time, **kwargs)

    def update(self, elapsed, dt) -> None:
        super().update(elapsed, dt)
        self.target_dot.update(dt)

    def begin(self) -> None:
        self.target_copy = self.target_dot
        super().begin()
    
    def finish(self) -> None:
        super().finish()
        self.scene.remove(self.start_dot)


class Indicate(Transform):
    def __init__(
        self, 
        item: Item,
        scale_factor: float = 1.2,
        color: JAnimColor = YELLOW,
        rate_func: RateFunc = there_and_back,
        **kwargs
    ) -> None:
        target = item.copy()
        target.scale(scale_factor).set_color(color)
        super().__init__(item, target, rate_func=rate_func, **kwargs)

    def finish(self) -> None:
        self.interpolate(0)


class Flash(AnimationGroup):
    def __init__(
        self,
        point_or_item: np.ndarray | Item,
        color: JAnimColor = YELLOW,
        line_length: float = 0.2,
        num_lines: int = 12,
        flash_radius: float = 0.3,
        line_stroke_width: float = 0.03,
        **kwargs
    ) -> None:
        self.point_or_item = point_or_item
        self.color = color
        self.line_length = line_length
        self.num_lines = num_lines
        self.flash_radius = flash_radius
        self.line_stroke_width = line_stroke_width

        self.lines = self.create_lines()
        animations = self.create_line_anims()
        super().__init__(
            *animations,
            **kwargs,
        )

    def create_lines(self) -> VGroup:
        lines = VGroup()
        for angle in np.arange(0, TAU, TAU / self.num_lines):
            line = Line(ORIGIN, self.line_length * RIGHT)
            line.shift((self.flash_radius - self.line_length) * RIGHT)
            line.rotate(angle, about_point=ORIGIN)
            lines.add(line)
        lines.set_stroke(
            color=self.color,
            width=self.line_stroke_width
        )
        lines.add_updater(lambda l: l.move_to(self.point_or_item))
        return lines

    def create_line_anims(self) -> list[Animation]:
        return [
            ShowCreationThenDestruction(line)
            for line in self.lines
        ]
    
    def begin(self) -> None:
        self.scene.add(self.lines)
        super().begin()
    
    def finish(self) -> None:
        super().finish()
        self.scene.remove(self.lines)


class CircleIndicate(Transform):
    def __init__(
        self, 
        item: Item,
        color: JAnimColor = YELLOW,
        rate_func: RateFunc = there_and_back,
        **kwargs
    ) -> None:
        start = Circle(color=color, opacity=0)
        start.add_updater(lambda c: c.surround(item))

        target = Circle(color=color)
        target.add_updater(lambda c: c.surround(item))

        super().__init__(start, target, rate_func=rate_func, **kwargs)

    def finish(self) -> None:
        self.interpolate(0)
        self.scene.remove(self.item)


class ShowPassingFlash(ShowPartial):
    def __init__(
        self,
        item: Item,
        time_width: float = 0.1,
        **kwargs
    ) -> None:
        self.time_width = time_width
        super().__init__(item, **kwargs)

    def get_bounds(self, alpha: float) -> tuple[float, float]:
        tw = self.time_width
        upper = interpolate(0, 1 + tw, alpha)
        lower = upper - tw
        upper = min(upper, 1)
        lower = max(lower, 0)
        return (lower, upper)

    def finish(self) -> None:
        super().finish()
        self.item_for_anim.set_visible(False)


# TODO: FlashAround
# TODO: FlashUnder


class ShowCreationThenDestruction(ShowPassingFlash):
    def __init__(
        self,
        item: Item,
        time_width: float = 2.0,
        run_time: float = 1,
        **kwargs
    ) -> None:
        super().__init__(item, time_width=time_width, run_time=run_time, **kwargs)


class ShowCreationThenFadeOut(Succession):
    def __init__(self, item: Item, **kwargs):
        super().__init__(
            ShowCreation(item),
            FadeOut(item),
            **kwargs
        )
    

class AnimationOnSurroundingRectangle(AnimationGroup):
    def __init__(
        self, 
        item: Item, 
        rect_anim: Animation,
        surrounding_rect_config: dict = {},
        **kwargs
    ) -> None:
        self.item_to_surround = item
        self.surrounding_rect_config = surrounding_rect_config

        rect = self.get_rect()
        rect.add(Point(rect.get_center()), is_helper=True)
        rect.add_updater(lambda r: r.shift(item.get_center() - r.helper_items[0].get_pos()))

        super().__init__(
            rect_anim(rect, **kwargs),
        )

    def get_rect(self) -> SurroundingRectangle:
        return SurroundingRectangle(
            self.item_to_surround,
            **self.surrounding_rect_config
        )


class ShowPassingFlashAround(AnimationOnSurroundingRectangle):
    def __init__(self, item: Item, **kwargs) -> None:
        super().__init__(item, ShowPassingFlash, **kwargs)


class ShowCreationThenDestructionAround(AnimationOnSurroundingRectangle):
    def __init__(self, item: Item, **kwargs) -> None:
        super().__init__(item, ShowCreationThenDestruction, **kwargs)


class ShowCreationThenFadeAround(AnimationOnSurroundingRectangle):
    def __init__(self, item: Item, **kwargs) -> None:
        super().__init__(item, ShowCreationThenFadeOut, **kwargs)


# TODO: ApplyWave
# TODO: WiggleOutThenIn
# TODO: TurnInsideOut
# TODO: FlashyFadeIn
