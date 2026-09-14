"""A method that means one thing on the class and another on an instance.

``Sandbox.list_snapshots()`` lists every snapshot in the current scope, while
``sandbox.list_snapshots()`` lists one sandbox's own - two different calls that
share a name. A single class namespace cannot hold both, so this descriptor
dispatches on whether the attribute was reached through an instance.
"""

from __future__ import annotations

from collections.abc import Callable
from types import MethodType
from typing import Any, Concatenate, Generic, ParamSpec, TypeVar, overload

ClassP = ParamSpec("ClassP")
ClassR = TypeVar("ClassR")
SelfP = ParamSpec("SelfP")
SelfR = TypeVar("SelfR")


class hybridmethod(Generic[ClassP, ClassR, SelfP, SelfR]):  # noqa: N801 - reads as a decorator
    """Bind the class implementation on the class, the instance one on instances."""

    def __init__(
        self,
        class_func: Callable[Concatenate[Any, ClassP], ClassR],
        instance_func: Callable[Concatenate[Any, SelfP], SelfR] | None = None,
    ) -> None:
        self._class_func = class_func
        self._instance_func = instance_func
        self.__doc__ = class_func.__doc__

    def instancemethod(
        self, func: Callable[Concatenate[Any, SelfP], SelfR]
    ) -> hybridmethod[ClassP, ClassR, SelfP, SelfR]:
        """Register the implementation used when reached through an instance."""
        return hybridmethod(self._class_func, func)

    @overload
    def __get__(self, obj: None, objtype: type[Any]) -> Callable[ClassP, ClassR]: ...

    @overload
    def __get__(self, obj: Any, objtype: type[Any] | None = None) -> Callable[SelfP, SelfR]: ...

    def __get__(self, obj: Any, objtype: type[Any] | None = None) -> Callable[..., Any]:
        if obj is None or self._instance_func is None:
            return MethodType(self._class_func, objtype)
        return MethodType(self._instance_func, obj)
