from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Generator, Self, overload

import janim.utils.refresh as refresh
from janim.exception import CmptGroupLookupError
from janim.utils.data import AlignedData

if TYPE_CHECKING:   # pragma: no cover
    from janim.items.item import Item


class _CmptMeta(type):
    def __new__(
        cls: type,
        name: str,
        bases: tuple[type, ...],
        attrdict: dict,
        *,
        impl=False,     # 若 impl=True，则会跳过下面的检查
    ):
        if not impl:
            for key in ('copy', 'become', 'not_changed'):
                if not callable(attrdict.get(key, None)):
                    raise AttributeError(f'Component 的每一个子类都必须继承并实现 `{key}` 方法，而 {name} 没有')
        return super().__new__(cls, name, bases, attrdict)


class Component[ItemT](refresh.Refreshable, metaclass=_CmptMeta):
    @dataclass
    class BindInfo:
        '''
        对组件定义信息的封装

        - ``decl_cls``: 以 ``xxx = CmptInfo(...)`` 的形式被声明在哪个类中；
          如果一个类及其父类都有 ``xxx = CmptInfo(...)`` ，那么 ``decl_cls`` 是父类
        - ``at_item``: 这个组件对象是属于哪个物件对象的
        - ``key``: 这个组件对象的变量名

        例：

        .. code-block:: python

            class MyCmpt(Component): ...

            class MyItem(Item):
                cmpt1 = CmptInfo(MyCmpt[Self])
                cmpt2 = CmptInfo(MyCmpt[Self])

            class MyItem2(MyItem):
                cmpt3 = CmptInfo(MyCmpt[Self])

            item = MyItem()

            # item.cmpt1.bind_info 与 BindInfo(MyItem, item, 'cmpt1') 一致
            # item.cmpt2.bind_info 与 BindInfo(MyItem, item, 'cmpt2') 一致

            item2 = MyItem2()

            # item2.cmpt1.bind_info 与 BindInfo(MyItem, item2, 'cmpt1') 一致
            # item2.cmpt3.bind_info 与 BindInfo(MyItem2, item2, 'cmpt3') 一致
        '''
        decl_cls: type[Item]
        at_item: Item
        key: str

    def __init__(self) -> None:
        super().__init__()
        self.bind: Component.BindInfo | None = None

    def init_bind(self, bind: BindInfo) -> None:
        '''
        用于 ``Item._init_components``

        子类可以继承该函数，进行与所在物件相关的处理
        '''
        self.bind = bind

    def fallback_check(self) -> bool:
        return self.bind is not None and self.bind.at_item.stored

    def mark_refresh(self, func: Callable | str, *, recurse_up=False, recurse_down=False) -> Self:
        '''
        详见： :meth:`~.Item.broadcast_refresh_of_component`
        '''
        super().mark_refresh(func)

        if self.bind is not None:
            self.bind.at_item.broadcast_refresh_of_component(
                self,
                func,
                recurse_up=recurse_up,
                recurse_down=recurse_down
            )

    def copy(self) -> Self:
        cmpt_copy = copy.copy(self)
        # cmpt_copy.bind = None
        cmpt_copy.reset_refresh()
        return cmpt_copy

    def become(self, other) -> Self: ...

    def not_changed(self, other) -> bool: ...

    def get_same_cmpt(self, item: Item) -> Self:
        return self.get_same_cmpt_if_exists(item) or getattr(item.astype(self.bind.decl_cls), self.bind.key)

    def get_same_cmpt_without_mock(self, item: Item) -> Self | None:
        return item.components.get(self.bind.key, None)

    def get_same_cmpt_if_exists(self, item: Item) -> Self | None:
        cmpt = item.components.get(self.bind.key, None)
        if cmpt is not None:
            return cmpt

        return item._astype_mock_cmpt.get(self.bind.key, None)

    def walk_same_cmpt_of_self_and_descendants_without_mock(
        self,
        root_only: bool = False,
        *,
        timed: bool = False
    ) -> Generator[Self, None, None]:
        yield self
        if root_only or self.bind is None:
            return

        item = self.bind.at_item
        walk = None
        if not item.stored:
            walk = item.walk_descendants(self.bind.decl_cls)
        elif timed:
            walk = item._walk_lst(self.bind.decl_cls, item._current_family(up=False))

        if walk is not None:
            for item in walk:
                cmpt = self.get_same_cmpt_without_mock(item)
                if cmpt is None:
                    continue
                yield cmpt

    @property
    def r(self) -> ItemT:
        '''
        所位于的物件，便于链式调用同物件下其它的组件
        '''
        return self.bind.at_item


class CmptInfo[T]:
    '''
    在类中定义组件需要使用该类

    例：

    .. code-block:: python

        class MyItem(Item):
            # Wrong!
            # cmpt1 = MyCmpt()

            # Right
            cmpt1 = CmptInfo(MyCmpt[Self])

            # Wrong!
            # cmpt2 = MyCmptWithArgs(1)

            # Right
            cmpt2 = CmptInfo(MyCmptWithArgs[Self], 1)
    '''
    def __init__(self, cls: type[T], *args, **kwargs):
        self.__doc__ = ""
        self.cls = getattr(cls, '__origin__', cls)
        self.args = args
        self.kwargs = kwargs

    def create(self) -> Component:
        return self.cls(*self.args, **self.kwargs)

    # 方便代码补全，没有实际意义
    @overload
    def __get__(self, obj: None, owner) -> Self: ...
    @overload
    def __get__(self, obj: object, owner) -> T: ...

    def __get__(self, obj, owner):
        return self


class _CmptGroup(Component):
    def __init__(self, cmpt_info_list: list[CmptInfo], **kwargs):
        super().__init__(**kwargs)
        self.cmpt_info_list = cmpt_info_list

    def init_bind(self, bind: Component.BindInfo) -> None:
        super().init_bind(bind)
        self._find_objects()

    def copy(self, *, new_cmpts: dict[str, Component]) -> Self:
        cmpt_copy = super().copy()
        cmpt_copy.objects = {
            key: new_cmpts[key]
            for key in cmpt_copy.objects.keys()
        }

        return cmpt_copy

    def become(self, other) -> Self:    # pragma: no cover
        return self

    def not_changed(self, other: _CmptGroup) -> bool:
        for key, obj in self.objects.items():
            if not obj.not_changed(other.objects[key]):
                return False

        return True

    @classmethod
    def align(cls, cmpt1: _CmptGroup, cmpt2: _CmptGroup, aligned: AlignedData[Item]):
        cmpt1_copy = cmpt1.copy(new_cmpts=aligned.data1.components)
        cmpt2_copy = cmpt2.copy(new_cmpts=aligned.data2.components)
        cmpt_union = cmpt1.copy(new_cmpts=aligned.union.components)
        return AlignedData(cmpt1_copy, cmpt2_copy, cmpt_union)

    def _find_objects(self) -> None:
        self.objects: dict[str, Component] = {}

        for cmpt_info in self.cmpt_info_list:
            key = self._find_key(cmpt_info)
            self.objects[key] = getattr(self.bind.at_item, key)

    def _find_key(self, cmpt_info: CmptInfo) -> str:
        from janim.items.item import CLS_CMPTINFO_NAME

        for key, val in self.bind.decl_cls.__dict__.get(CLS_CMPTINFO_NAME, {}).items():
            if val is cmpt_info:
                return key

        raise CmptGroupLookupError('CmptGroup 必须要与传入的内容在同一个类的定义中')

    def __getattr__(self, name: str):
        if name == 'objects':
            raise AttributeError()

        objects = []
        methods = []

        for obj in self.objects.values():
            if not hasattr(obj, name):
                continue

            attr = getattr(obj, name)
            if not callable(attr):
                continue

            objects.append(obj)
            methods.append(attr)

        if not methods:
            cmpt_str = ', '.join(cmpt.__class__.__name__ for cmpt in self.objects)
            raise AttributeError(f'({cmpt_str}) 中没有组件有叫作 {name} 的方法')

        def wrapper(*args, **kwargs):
            ret = [
                method(*args, **kwargs)
                for method in methods
            ]

            return self if all(a is b for a, b in zip(ret, objects)) else ret

        return wrapper


def CmptGroup[T](*cmpt_info_list: CmptInfo[T]) -> CmptInfo[T]:
    '''
    用于将多个组件打包，使得可以同时调用

    例：

    .. code-block:: python

        class MyItem(Item):
            stroke = CmptInfo(Cmpt_Rgbas[Self])
            fill = CmptInfo(Cmpt_Rgbas[Self])
            color = CmptGroup(stroke, fill)

        item = MyItem()
        item.stroke.set(...)    # 只有 stroke 的被调用 | Only the method of stroke be called
        item.color.set(...)     # stroke 和 fill 的都被调用了 | the methods of stroke and fill are both called
    '''
    return CmptInfo(_CmptGroup, cmpt_info_list)
