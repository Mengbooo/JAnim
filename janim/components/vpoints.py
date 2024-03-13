from __future__ import annotations

from typing import Callable, Generator, Iterable, Self

import numpy as np

import janim.utils.refresh as refresh
from janim.components.points import Cmpt_Points
from janim.constants import NAN_POINT, OUT, RIGHT
from janim.exception import PointError
from janim.items.item import Item
from janim.logger import log
from janim.typing import VectArray
from janim.utils.bezier import (PathBuilder, bezier, integer_interpolate,
                                inverse_interpolate,
                                partial_quadratic_bezier_points)
from janim.utils.data import AlignedData
from janim.utils.space_ops import get_norm, get_unit_normal


class Cmpt_VPoints[ItemT](Cmpt_Points[ItemT]):
    '''
    曲线点坐标数据

    - 每三个点表示一段二阶贝塞尔曲线，并且前后相接的曲线共用公共点。

      例如对于点坐标列表 ``[a, b, c, d, e, f, g]``，则表示这些曲线：``[a, b, c]`` ``[c, d, e]`` ``[e, f, g]``

    - 将 ``NAN_POINT`` 视为子路径结束的表示。

      例如对于点坐标列表 ``[a, b, c, d, e, NAN_POINT, f, g, h]``，则表示两段子路径：``[a, b, c, d, e]`` 和 ``[f, g, h]``

    - 如果子路径的终止点和起始点相同，则该段子路径被视为闭合路径。

      只有闭合的子路径，才能够进行填充色的渲染
    '''
    def copy(self) -> Self:
        return super().copy()

    def become(self, other: Cmpt_VPoints) -> Self:
        super().become(other)
        return self

    def __eq__(self, other: Cmpt_VPoints) -> bool:
        return super().__eq__(other)

    def set(self, points: VectArray) -> Self:
        if len(points) != 0 and len(points) % 2 == 0:
            log.warning(f'设置的点数量为 {len(points)}，不是奇数，最后一个点被忽略')
            points = points[:-1]
        super().set(points)
        return self

    # region align

    @classmethod
    def align_for_interpolate(cls, cmpt1: Cmpt_VPoints, cmpt2: Cmpt_VPoints) -> AlignedData[Self]:
        cmpt1_copy = cmpt1.copy()
        cmpt2_copy = cmpt2.copy()

        if cmpt1_copy == cmpt2_copy:
            return AlignedData(cmpt1_copy, cmpt2_copy, cmpt1_copy.copy())

        if not cmpt1_copy.has():
            cmpt1_copy.set([cmpt2.box.center])
        if not cmpt2_copy.has():
            cmpt2_copy.set([cmpt1.box.center])

        subpaths1 = cmpt1_copy.get_subpaths()
        subpaths2 = cmpt2_copy.get_subpaths()

        # 如果都只有单个路径，直接对齐就可以了
        # 否则进行路径之间的配对，以便对齐数据
        if len(subpaths1) == len(subpaths2) == 1:
            sp1, sp2 = cls.align_path(subpaths1[0], subpaths2[0])
            cmpt1_copy.set(sp1)
            cmpt2_copy.set(sp2)
        else:
            # 这里使得 subpaths1 的子路径数量比 subpaths2 多，简化后面的判断
            reverse = len(subpaths1) < len(subpaths2)
            if reverse:
                cmpt1_copy, cmpt2_copy = cmpt2_copy, cmpt1_copy
                subpaths1, subpaths2 = subpaths2, subpaths1

            # 用于计算相对距离的“中心”
            # 这里的 ``RIGHT * (i * 1e-5)`` 是为了是有重合的点有所差别，比如可以保证图形字符 “O” 配对时的一致性
            def center(i: int, points: np.ndarray) -> np.ndarray:
                min = np.min(points, axis=0)
                max = np.max(points, axis=0)
                return (min + max) * 0.5 + RIGHT * (i * 1e-5)

            # 这里的 ``/ .box.width`` 是为了缩放到一致
            subpaths1_center = np.array([center(i, subpath) for i, subpath in enumerate(subpaths1)])
            subpaths1_center -= cmpt1_copy.box.center
            if cmpt1_copy.box.width != 0:
                subpaths1_center /= cmpt1_copy.box.width
            subpaths2_center = np.array([center(i, subpath) for i, subpath in enumerate(subpaths2)])
            subpaths2_center -= cmpt2_copy.box.center
            if cmpt2_copy.box.width != 0:
                subpaths2_center /= cmpt2_copy.box.width

            # 这两个函数使用曼哈顿距离
            def nearest_idx(point: np.ndarray) -> int:
                return abs(subpaths2_center - point).sum(axis=1).argmin()

            def sorted_idx(point: np.ndarray) -> Iterable[int]:
                return abs(subpaths2_center - point).sum(axis=1).argsort()

            type SubPath1Idx = int

            distributions: list[list[SubPath1Idx]] = [[] for _ in range(len(subpaths2))]

            # 将 subpaths1 按照最近原则分配给 subpaths2
            for idx1, center1 in enumerate(subpaths1_center):
                distributions[nearest_idx(center1)].append(idx1)

            # 遍历分配结果，如果发现有 subpaths2 中的子路径没有分配到内容，则从其它子路径那边抢一个来
            for idx2, distri in enumerate(distributions):
                # 如果有分配到内容，则跳过
                if distri:
                    continue

                # 按距离遍历其它的子路径
                for other_idx in sorted_idx(subpaths2_center[idx2]):
                    if other_idx == idx2:
                        continue

                    # 如果其它子路径有两个以上的分配，则从它这里抢一个
                    other_distri = distributions[other_idx]
                    if len(other_distri) >= 2:
                        distri.append(other_distri.pop(0))
                        break

                # 一定能抢到，所以执行到这里时 distri 应当不为空
                assert distri

            # 构建新的子路径
            new_subpaths1 = []
            new_subpaths2 = []

            for idx2, distri in enumerate(distributions):
                sp2_orig = subpaths2[idx2]

                # 得到点的数量最匹配的那一组，这样可以尽可能减少插入点的数量
                diff = np.array([len(subpaths1[idx1]) for idx1 in distri]) - len(sp2_orig)
                perfect = distri[abs(diff).argmin()]

                for idx1 in distri:
                    sp1 = subpaths1[idx1]
                    if idx1 == perfect:
                        sp2 = sp2_orig
                    else:
                        # 对于额外的路径，先创建回环
                        sp2 = np.vstack([sp2_orig[:-1], sp2_orig[::-1]])

                    sp1, sp2 = cls.align_path(sp1, sp2)
                    if new_subpaths1:
                        # 标记前一个路径结束
                        new_subpaths1.append(NAN_POINT)
                        new_subpaths2.append(NAN_POINT)
                    new_subpaths1.append(sp1)
                    new_subpaths2.append(sp2)

            cmpt1_copy.set(np.vstack(new_subpaths1))
            cmpt2_copy.set(np.vstack(new_subpaths2))

            # 换回来
            if reverse:
                cmpt1_copy, cmpt2_copy = cmpt2_copy, cmpt1_copy

        return AlignedData(cmpt1_copy, cmpt2_copy, cmpt1_copy.copy())

    @staticmethod
    def align_path(path1: np.ndarray, path2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        diff = abs(len(path1) - len(path2)) // 2
        if diff == 0:
            return path1, path2

        return (
            (Cmpt_VPoints.insert_n_curves_to_point_list(diff, path1), path2)
            if len(path1) < len(path2)
            else (path1, Cmpt_VPoints.insert_n_curves_to_point_list(diff, path2))
        )

    @staticmethod
    def insert_n_curves_to_point_list(n: int, points: VectArray) -> np.ndarray:
        if len(points) == 1:
            return np.repeat(points, 2 * n + 1, 0)

        bezier_tuples = list(Cmpt_VPoints.get_bezier_tuples_from_points(points))
        norms = [
            0 if np.isnan(tup[1][0]) else get_norm(tup[2] - tup[0])
            for tup in bezier_tuples
        ]
        # Calculate insertions per curve (ipc)
        ipc = np.zeros(len(bezier_tuples), dtype=int)
        for _ in range(n):
            index = np.argmax(norms)
            ipc[index] += 1
            norms[index] *= ipc[index] / (ipc[index] + 1)

        new_points = [points[0]]
        for tup, n_inserts in zip(bezier_tuples, ipc):
            # What was once a single quadratic curve defined
            # by "tup" will now be broken into n_inserts + 1
            # smaller quadratic curves
            alphas = np.linspace(0, 1, n_inserts + 2)
            for a1, a2 in zip(alphas, alphas[1:]):
                new_points.extend(partial_quadratic_bezier_points(tup, a1, a2)[1:])

        return np.vstack(new_points)

    # endregion

    # region anchors and handles

    def get_anchors(self) -> np.ndarray:
        '''
        得到曲线的锚点
        '''
        return self.get()[::2]

    def get_handles(self) -> np.ndarray:
        '''
        得到曲线的控制点
        '''
        return self.get()[1::2]

    @property
    def start_direction(self) -> np.ndarray:
        points = self._points._data
        start = points[0]
        for pos in points[1:]:
            if not np.isclose(start, pos).all():
                return pos - start
        return RIGHT

    @property
    def end_direction(self) -> np.ndarray:
        points = self._points._data
        end = points[-1]
        for pos in points[-2::-1]:
            if not np.isclose(end, pos).all():
                return end - pos
        return RIGHT

    def close_path(self) -> Self:
        self._raise_error_if_no_points()
        indices = self.get_subpath_end_indices()
        end = self.get_end()
        if len(indices) == 1:
            point = self.get_start()
        else:
            point = self._points._data[indices[-2] + 2]
        self.extend([(end + point) * 0.5, point])
        return self

    @staticmethod
    def get_bezier_tuples_from_points(points: VectArray) -> Iterable[np.ndarray]:
        '''
        由 ``points`` 得到由每一组贝塞尔曲线控制点组成的列表

        例如，对于有 7 个点的 ``points``，返回值是 ``(points[[0, 1, 2]], points[[2, 3, 4]], points[[4, 5, 6]])``
        '''
        n_curves = max(0, len(points) - 1) // 2
        return (points[2 * i: 2 * i + 3] for i in range(n_curves))

    def get_bezier_tuples(self) -> Iterable[np.ndarray]:
        '''
        得到由每一组贝塞尔曲线控制点组成的列表，具体参考 :meth:`get_bezier_tuples_from_points`
        '''
        return self.get_bezier_tuples_from_points(self.get())

    def curves_count(self) -> int:
        '''
        得到曲线数量
        '''
        return max(0, self.count() - 1) // 2

    def get_nth_curve_points(self, n: int) -> VectArray:
        '''
        得到第 ``n`` 组的贝塞尔曲线控制点 (从 0 开始计数)
        '''
        if n < 0 or n >= self.curves_count():
            raise PointError(f'n 必须是 0~{self.curves_count() - 1} 的值，{n} 无效')
        return self._points._data[2 * n: 2 * n + 3].copy()

    def get_nth_curve_function(self, n: int) -> Callable[[float], np.ndarray]:
        '''
        返回值是第 ``n`` 组贝塞尔曲线的描点函数，传入 [0, 1] 之间的值，得到对应的在曲线上的点
        '''
        return bezier(self.get_nth_curve_points(n))

    def quick_point_from_proportion(self, alpha: float) -> np.ndarray:
        '''
        相比 :meth:`point_from_proportion` 而言，更快

        但是这里假设所有的曲线都有相同的长度，所以是不准确的
        '''
        num_curves = self.curves_count()
        n, residue = integer_interpolate(0, num_curves, alpha)
        curve_func = self.get_nth_curve_function(n)
        return curve_func(residue)

    def curve_and_prop_of_partial_point(self, alpha: float) -> tuple[int, float]:
        '''
        如果你想要得到沿着整个曲线上所在比例为 alpha 处的点，
        这个函数会返回这个比例所对应的曲线部分的索引，以及在这个曲线部分上需要行进的比例
        '''
        if alpha == 0:
            return (0, 0.0)
        partials: list[float] = [0]
        for tup in self.get_bezier_tuples():
            if (tup[0] == tup[1]).all():
                # Don't consider null curves
                arclen = 0
            else:
                # Approximate length with straight line from start to end
                arclen = get_norm(tup[2] - tup[0])
            partials.append(partials[-1] + arclen)
        full = partials[-1]
        if full == 0:
            return len(partials), 1.0
        # First index where the partial length is more than alpha times the full length
        index = next(
            (i for i, x in enumerate(partials) if x >= full * alpha),
            len(partials) - 1  # Default
        )
        residue = float(inverse_interpolate(
            partials[index - 1] / full, partials[index] / full, alpha
        ))
        return index - 1, residue

    def point_from_proportion(self, alpha: float) -> np.ndarray:
        if alpha <= 0:
            return self.get_start()
        elif alpha >= 1:
            return self.get_end()
        index, residue = self.curve_and_prop_of_partial_point(alpha)
        return self.get_nth_curve_function(index)(residue)

    def pointwise_become_partial(self, other: Cmpt_VPoints | Item, a: float, b: float) -> Self:
        if isinstance(other, Item):
            cmpt = self.get_same_cmpt(other)
        else:
            cmpt = other

        points = cmpt.get()
        if a <= 0 and b >= 1:
            self.set(points)
            return self
        num_curves = cmpt.curves_count()

        # Partial curve includes three portions:
        # - A start, which is some ending portion of an inner quadratic
        # - A middle section, which matches the curve exactly
        # - An end, which is the starting portion of a later inner quadratic

        lower_index, lower_residue = integer_interpolate(0, num_curves, a)
        upper_index, upper_residue = integer_interpolate(0, num_curves, b)
        i1 = 2 * lower_index
        i2 = 2 * lower_index + 3
        i3 = 2 * upper_index
        i4 = 2 * upper_index + 3

        new_points = points.copy()
        if num_curves == 0:
            new_points[:] = 0
            return self
        if lower_index == upper_index:
            tup = partial_quadratic_bezier_points(points[i1:i2], lower_residue, upper_residue)
            new_points[:i1] = tup[0]
            new_points[i1:i4] = tup
            new_points[i4:] = tup[2]
        else:
            low_tup = partial_quadratic_bezier_points(points[i1:i2], lower_residue, 1)
            high_tup = partial_quadratic_bezier_points(points[i3:i4], 0, upper_residue)
            new_points[0:i1] = low_tup[0]
            new_points[i1:i2] = low_tup
            # Keep new_points i2:i3 as they are
            new_points[i3:i4] = high_tup
            new_points[i4:] = high_tup[2]
        self.set(new_points)
        return self

    # endregion

    # region _as_corners 操作

    def add_as_corners(self, points: VectArray) -> Self:
        '''
        以折线的方式将 ``points`` 添加
        '''
        if not self.has():
            self.set(points[0])

        builder = PathBuilder(start_point=self.get_end())
        for point in points:
            builder.line_to(point)
        self.extend(builder.get()[1:])

        return self

    def set_as_corners(self, points: VectArray) -> Self:
        '''
        将点数据设置为由 ``points`` 构成的折线
        '''
        builder = PathBuilder(start_point=points[0])
        for point in points[1:]:
            builder.line_to(point)
        self.set(builder.get())

    # endregion

    # TODO: make_smooth

    # region unit_normal

    @staticmethod
    def get_area_vector_from_points(points: np.ndarray) -> np.ndarray:
        if len(points) == 0:
            return np.zeros(3)

        p0 = points[::2]
        p1 = np.roll(p0, -1, axis=0)

        # Each term goes through all edges [(x0, y0, z0), (x1, y1, z1)]
        sums = p0 + p1
        diffs = p1 - p0
        return 0.5 * np.array([
            (sums[:, 1] * diffs[:, 2]).sum(),  # Add up (y0 + y1)*(z1 - z0)
            (sums[:, 2] * diffs[:, 0]).sum(),  # Add up (z0 + z1)*(x1 - x0)
            (sums[:, 0] * diffs[:, 1]).sum(),  # Add up (x0 + x1)*(y1 - y0)
        ])

    @property
    @Cmpt_Points.set.self_refresh()
    @refresh.register
    def area_vector(self) -> np.ndarray:
        '''
        一个向量，其长度为锚点形成的多边形所围成的面积，根据右手定则指向垂直于该多边形的方向
        '''
        return self.get_area_vector_from_points(self.get())

    @property
    @Cmpt_Points.set.self_refresh()
    @refresh.register
    def unit_normal(self) -> np.ndarray:
        if self.count() < 3:
            return OUT

        area_vect = self.area_vector
        area = get_norm(area_vect)
        if area > 0:
            return area_vect / area

        points = self.get()
        return get_unit_normal(
            points[1] - points[0],
            points[2] - points[1]
        )

    # endregion

    # region subpaths

    def walk_subpath_end_indices(self) -> Generator[int, None, None]:
        '''
        遍历每个子路径结尾的下标
        '''
        points = self.get()
        handles = points[1::2]
        yield from np.where(np.isnan(handles[:, 0]))[0] * 2
        yield len(points) - 1

    def get_subpath_end_indices(self) -> list[int]:
        return list(self.walk_subpath_end_indices())

    def get_closepath_flags(self) -> np.ndarray:
        '''
        得到子路径是否闭合的标志，结果长度与点数量相同

        对于闭合路径，结果中对应部分会被设置为 ``True``
        '''
        result = np.full(self.count(), False)
        if len(result) == 0:
            return result

        points = self.get()

        start_idx = 0
        for end_idx in self.walk_subpath_end_indices():
            if np.isclose(points[end_idx], points[start_idx]).all():
                result[start_idx: end_idx + 1] = True
            start_idx = end_idx + 2

        return result

    @staticmethod
    def get_parts_by_end_indices(array: np.ndarray, end_indices: np.ndarray) -> list[np.ndarray]:
        '''
        根据子路径结尾下标的列表，将 ``array`` 分段
        '''
        if len(array) == 0:
            return []
        start_indices = [0, *(end_indices[:-1] + 2)]
        return [array[i1: i2 + 1] for i1, i2 in zip(start_indices, end_indices)]

    def get_subpaths(self) -> list[np.ndarray]:
        '''
        得到子路径列表
        '''
        return self.get_parts_by_end_indices(self.get(), np.array(self.get_subpath_end_indices()))

    # endregion
