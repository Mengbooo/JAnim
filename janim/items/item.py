from __future__ import annotations

import copy
import inspect
import itertools as it
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Self, overload

from janim.components.component import CmptInfo, Component, _CmptGroup
from janim.components.depth import Cmpt_Depth
from janim.exception import AsTypeError
from janim.items.relation import Relation
from janim.logger import log
from janim.render.base import Renderer
from janim.typing import SupportsApartAlpha, SupportsInterpolate
from janim.utils.data import AlignedData
from janim.utils.iterables import resize_preserving_order
from janim.utils.paths import PathFunc, straight_path

if TYPE_CHECKING:
    from janim.items.points import Group

type DynamicItem = Callable[[float], Item]

CLS_CMPTINFO_NAME = '__cls_cmptinfo'
CLS_STYLES_NAME = '__cls_styles'
ALL_STYLES_NAME = '__all_styles'


class _ItemMeta(type):
    '''
    作为 metaclass 记录定义在类中的所有 CmptInfo
    '''
    def __new__(cls: type, name: str, bases: tuple[type, ...], attrdict: dict):
        # 记录所有定义在类中的 CmptInfo
        cls_components: dict[str, Component] = {
            key: val
            for key, val in attrdict.items()
            if isinstance(val, CmptInfo)
        }
        attrdict[CLS_CMPTINFO_NAME] = cls_components

        # 记录 set_style 的参数
        set_style_func = attrdict.get('set_style', None)
        if set_style_func is not None and callable(set_style_func):
            sig = inspect.signature(set_style_func)
            styles_name: list[str] = [
                param.name
                for param in list(sig.parameters.values())[1:]
                if param.kind not in (param.POSITIONAL_ONLY, param.VAR_POSITIONAL, param.VAR_KEYWORD)
            ]
            attrdict[CLS_STYLES_NAME] = styles_name

            all_styles = list(it.chain(
                styles_name,
                *[
                    getattr(base, CLS_STYLES_NAME)
                    for base in bases
                    if hasattr(base, CLS_STYLES_NAME)
                ]
            ))
            attrdict[ALL_STYLES_NAME] = all_styles

        return super().__new__(cls, name, bases, attrdict)


class Item(Relation['Item'], metaclass=_ItemMeta):
    renderer_cls = Renderer
    '''
    覆盖该值以在子类中使用特定的渲染器
    '''

    global_renderer: Renderer | None = None
    '''
    共用的渲染器，用于 ``is_temporary=True`` 的物件
    '''

    depth = CmptInfo(Cmpt_Depth[Self], 0)

    def __init__(
        self,
        *args,
        children: list[Item] | None = None,
        **kwargs
    ):
        super().__init__(*args)

        self.stored: bool = False
        self.stored_parents: list[Item] | None = None
        self.stored_children: list[Item] | None = None

        # 如果 is_temporary 为 True，则不会另外创建渲染器，而是使用共用的渲染器
        self.is_temporary: bool = False

        from janim.anims.timeline import Timeline
        self.timeline = Timeline.get_context(raise_exc=False)

        self._init_components()

        self._astype: type[Item] | None = None
        self._astype_mock_cmpt: dict[str, Component] = {}

        self.renderer: Renderer | None = None

        if children is not None:
            self.add(*children)
        self.digest_styles(**kwargs)

    @dataclass
    class _CmptInitData:
        info: CmptInfo[CmptInfo]
        decl_cls: type[Item]

    def _init_components(self) -> None:
        '''
        创建出 CmptInfo 对应的 Component，
        并以同名存于对象中，起到在名称上覆盖类中的 CmptInfo 的效果

        因为 CmptInfo 的 __get__ 标注的返回类型是对应的 Component，
        所以以上做法没有影响基于类型标注的代码补全
        '''
        type CmptKey = str

        datas: dict[CmptKey, Item._CmptInitData] = {}

        for cls in reversed(self.__class__.mro()):
            for key, info in cls.__dict__.get(CLS_CMPTINFO_NAME, {}).items():
                info: CmptInfo
                if key in datas:
                    datas[key].info = info
                else:  # key not in datas
                    datas[key] = self._CmptInitData(info, cls)

        self.components: dict[str, Component] = {}

        for key, data in datas.items():
            obj = data.info.create()
            obj.init_bind(Component.BindInfo(data.decl_cls, self, key))

            self.__dict__[key] = self.components[key] = obj

    def set_component(self, key: str, cmpt: Component) -> None:
        setattr(self, key, cmpt)
        self.components[key] = cmpt

    def broadcast_refresh_of_component(
        self,
        cmpt: Component,
        func: Callable | str,
        *,
        recurse_up=False,
        recurse_down=False,
    ) -> Self:
        '''
        为 :meth:`~.Component.mark_refresh()`
        进行 ``recurse_up/down`` 的处理
        '''
        if not recurse_up and not recurse_down:
            return

        def mark(items: list[Item]):
            for item in items:
                if isinstance(item, cmpt.bind.decl_cls):
                    # 一般情况
                    item_cmpt: Component = getattr(item, cmpt.bind.key)
                    item_cmpt.mark_refresh(func)

                else:
                    # astype 情况
                    mock_cmpt = item._astype_mock_cmpt.get(cmpt.bind.key, None)
                    if mock_cmpt is not None:
                        mock_cmpt.mark_refresh(func)

        if recurse_up:
            mark(self.ancestors())

        if recurse_down:
            mark(self.descendants())

    def digest_styles(self, **styles):
        '''
        设置物件以及子物件的样式
        '''
        flags = dict.fromkeys(styles.keys(), False)
        for item in self.walk_self_and_descendants():
            available_styles = item.get_available_styles()
            apply_styles = {
                key: style
                for key, style in styles.items()
                if key in available_styles
            }
            for key in apply_styles:
                flags[key] = True
            item.set_style(**apply_styles)

        for key, flag in flags.items():
            if not flag:
                log.warning(f'传入参数 "{key}" 没有匹配任何的样式设置，且没有被任何地方使用')

    @classmethod
    def get_available_styles(cls) -> list[str]:
        return getattr(cls, ALL_STYLES_NAME)

    def set_style(
        self,
        depth: float | None = None,
        **kwargs
    ) -> Self:
        '''
        设置物件自身的样式，不影响子物件
        '''
        if depth is not None:
            self.depth.set(depth)
        return self

    def do(self, func: Callable[[Self], Any]) -> Self:
        '''
        使用 ``func`` 对物件进行操作，并返回 ``self`` 以方便链式调用
        '''
        func(self)
        return self

    def is_null(self) -> bool:
        return False

    @property
    def anim(self) -> Self:
        '''
        例如：

        .. code-block:: python

            self.play(
                item.anim.points.scale(2).r.color.set('green')
            )

        该例子会创建将 ``item`` 缩放 2 倍并且设置为绿色的补间动画

        并且可以向动画传入参数：

        .. code-block:: python

            self.play(
                item.anim(duration=2, rate_func=linear)
                .points.scale(2).r.color.set('green')
            )

        ``.r`` 表示从组件回到物件，这样就可以调用其它组件的功能
        '''
        from janim.anims.transform import MethodTransformArgsBuilder
        return MethodTransformArgsBuilder(self)

    # 使得 .anim() 后仍有代码提示
    @overload
    def __call__(self, **kwargs) -> Self: ...

    @overload
    def __getitem__(self, value: int) -> Item: ...
    @overload
    def __getitem__(self, value: slice) -> Group: ...

    def __getitem__(self, value):
        if isinstance(value, slice):
            from janim.items.points import Group
            return Group(*self.children[value])
        return self.children[value]

    def __iter__(self):
        return iter(self.children)

    def __len__(self) -> int:
        return len(self.children)

    def __mul__(self, other: int) -> Group[Self]:
        assert isinstance(other, int)
        return self.replicate(other)

    def replicate(self, n: int) -> Group[Self]:
        '''
        复制 n 个自身，并作为一个 :class:`Group` 返回

        可以将 ``item * n`` 作为该方法的简写
        '''
        from janim.items.points import Group
        return Group(
            *(self.copy() for i in range(n))
        )

    # region astype

    def astype[T](self, cls: type[T]) -> T:
        '''
        使得可以调用当前物件中没有的组件

        例：

        .. code-block:: python

            group = Group(
                Rect()
                Circle()
            )

        在这个例子中，并不能 ``group.color.set(BLUE)`` 来设置子物件中的颜色，
        但是可以使用 ``group.astype(VItem).color.set(BLUE)`` 来做到

        也可以使用简写 ``group(VItem).color.set(BLUE)``
        '''
        if not isinstance(cls, type) or not issubclass(cls, Item):
            # TODO: i18n
            raise AsTypeError(f'{cls.__name__} 不是以 Item 为基类，无法作为 astype 的参数')

        self._astype = cls
        return self

    @overload
    def __call__[T](self, cls: type[T]) -> T: ...

    def __call__[T](self, cls: type[T]) -> T:
        '''
        等效于调用 ``astype``
        '''
        return self.astype(cls)

    def __getattr__(self, name: str):
        if name == '__setstate__':
            raise AttributeError()

        cmpt_info = None if self._astype is None else getattr(self._astype, name, None)
        if not isinstance(cmpt_info, CmptInfo):
            super().__getattribute__(name)  # raise error

        # 找到 cmpt_info 是在哪个类中被定义的
        decl_cls: type[Item] | None = None
        for sup in self._astype.mro():
            if name in sup.__dict__.get(CLS_CMPTINFO_NAME, {}):
                decl_cls = sup

        assert decl_cls is not None

        # 如果 self 本身就是 decl_cls 的实例
        # 那么自身肯定有名称为 name 的组件，对于这种情况实际上完全没必要 astype
        # 为了灵活性，这里将这个已有的组件返回
        if isinstance(self, decl_cls):
            return getattr(self, name)

        cmpt = self._astype_mock_cmpt.get(name, None)

        # 如果 astype 需求的组件已经被创建过，并且新类型不是旧类型的子类，那么直接返回
        if cmpt is not None and (not issubclass(cmpt_info.cls, cmpt.__class__) or cmpt_info.cls is cmpt.__class__):
            return cmpt

        # astype 需求的组件还没创建，那么创建并记录
        cmpt = cmpt_info.create()
        cmpt.init_bind(Component.BindInfo(decl_cls, self, name))

        self._astype_mock_cmpt[name] = cmpt
        return cmpt

    # endregion

    # region data

    def get_parents(self):
        return self.stored_parents if self.stored else self.parents

    def get_children(self):
        return self.stored_children if self.stored else self.children

    def not_changed(self, other: Self) -> bool:
        if self.get_children() != other.get_children():
            return False
        for key, cmpt in self.components.items():
            if not cmpt.not_changed(other.components[key]):
                return False
        return True

    def current(self, *, as_time: float | None = None, skip_dynamic=False) -> Self:
        '''
        当前物件

        - 如果此时在回放和 Updater 中，则返回对应时间的历史物件
        - 在其余情况下，包括该物件没有历史记录的情况，则返回物件自身
        '''
        return self.timeline.item_current(self, as_time=as_time, skip_dynamic=skip_dynamic)

    def copy(self, *, root_only=False) -> Self:
        '''
        复制物件
        '''
        copy_item = copy.copy(self)

        copy_item.reset_refresh()

        copy_item.parents = []
        copy_item.children = []
        if root_only:
            copy_item.stored = True
            copy_item.stored_parents = self.get_parents().copy()
            copy_item.stored_children = self.get_children().copy()
        else:
            copy_item.add(*[item.copy() for item in self.children])
            copy_item.parents_changed()

        new_cmpts = {}
        for key, cmpt in self.components.items():
            if isinstance(cmpt, _CmptGroup):
                # 因为现在的 Python 版本中，dict 取键值保留原序
                # 所以 new_cmpts 肯定有 _CmptGroup 所需要的
                cmpt_copy = cmpt.copy(new_cmpts=new_cmpts)
            else:
                cmpt_copy = cmpt.copy()

            if cmpt.bind is not None:
                cmpt_copy.init_bind(Component.BindInfo(cmpt.bind.decl_cls,
                                                       copy_item,
                                                       key))

            new_cmpts[key] = cmpt_copy
            setattr(copy_item, key, cmpt_copy)

        copy_item.components = new_cmpts
        copy_item._astype_mock_cmpt = {}

        return copy_item

    # TODO: optimize
    def _current_family(self, *, up: bool) -> list[Item]:   # use DFS
        lst = self.stored_parents if up else self.stored_children
        res = []

        for sub_obj in lst:
            current = sub_obj.current()
            if current not in res:
                res.append(current)
            res.extend(filter(
                lambda obj: obj not in res,
                current._current_family(up=up)
                if current.stored
                else (current.ancestors() if up else current.descendants())
            ))

        return res

    def become(self, other: Item) -> Self:
        '''
        将该物件的数据设置为与传入的物件相同（以复制的方式，不是引用）
        '''
        # self.parents 不变

        children = self.children.copy()
        self.clear_children()
        for old, new in it.zip_longest(children, other.children):
            if new is None:
                break
            if old is None or type(old) is not type(new):
                self.add(new.copy())
            else:
                self.add(old.become(new))

        for key in self.components.keys() | other.components.keys():
            self.components[key].become(other.components[key])

        from janim.anims.timeline import Timeline
        timeline = Timeline.get_context(raise_exc=False)
        if timeline is not None and timeline.is_displaying(self):
            timeline.show(self)

        return self

    def store(self) -> Self:
        return self.copy(root_only=True)

    def restore(self, other: Item) -> Self:
        self.parents = other.parents.copy()
        self.parents_changed()
        self.children = other.children.copy()
        self.children_changed()

        for key in self.components.keys() & other.components.keys():
            self.components[key].become(other.components[key])

        return self

    @classmethod
    def align_for_interpolate(
        cls,
        item1: Item,
        item2: Item
    ) -> AlignedData[Self]:
        '''
        进行数据对齐，以便插值
        '''
        aligned = AlignedData(item1.store(),
                              item1.store(),
                              item2.store())

        # align components
        for key, cmpt1 in item1.components.items():
            cmpt2 = item2.components.get(key, None)

            if isinstance(cmpt1, _CmptGroup) and isinstance(cmpt2, _CmptGroup):
                cmpt_aligned = cmpt1.align(cmpt1, cmpt2, aligned)

            elif cmpt2 is None or not isinstance(cmpt1, SupportsInterpolate):
                cmpt_aligned = AlignedData(cmpt1, cmpt1, cmpt1)
            else:
                cmpt_aligned = cmpt1.align_for_interpolate(cmpt1, cmpt2)

            aligned.data1.set_component(key, cmpt_aligned.data1)
            aligned.data2.set_component(key, cmpt_aligned.data2)
            aligned.union.set_component(key, cmpt_aligned.union)

        # align children
        max_len = max(len(item1.get_children()), len(item2.get_children()))
        aligned.data1.stored_children = resize_preserving_order(item1.get_children(), max_len)
        aligned.data2.stored_children = resize_preserving_order(item2.get_children(), max_len)

        return aligned

    def interpolate(
        self,
        item1: Item,
        item2: Item,
        alpha: float,
        *,
        path_func: PathFunc = straight_path,
    ) -> None:
        '''
        进行插值（仅对该物件进行，不包含后代物件）
        '''
        for key, cmpt in self.components.items():
            cmpt1 = item1.components[key]
            cmpt2 = item2.components[key]

            if not isinstance(cmpt, SupportsInterpolate):
                continue

            cmpt.interpolate(cmpt1, cmpt2, alpha, path_func=path_func)

    def apart_alpha(self, n: int) -> None:
        for cmpt in self.components.values():
            if isinstance(cmpt, SupportsApartAlpha):
                cmpt.apart_alpha(n)

    @classmethod
    def get_global_renderer(cls) -> None:
        if cls.global_renderer is None:
            cls.global_renderer = cls.renderer_cls()
        return cls.global_renderer

    def create_renderer(self) -> None:
        if self.is_temporary:
            self.renderer = self.get_global_renderer()
        else:
            self.renderer = self.renderer_cls()

    def render(self) -> None:
        if self.renderer is None:
            self.create_renderer()

        if not self.renderer.initialized:
            self.renderer.init()
            self.renderer.initialized = True
        self.renderer.render(self)

    # endregion

    # region timeline

    def show(self, **kwargs) -> Self:
        '''
        显示物件
        '''
        self.timeline.show(self, **kwargs)
        return self

    def hide(self, **kwargs) -> Self:
        '''
        隐藏物件
        '''
        self.timeline.hide(self, **kwargs)
        return self

    # endregion
