"""Tiny printer the examples share."""

from __future__ import annotations


def log(message: str = "") -> None:
    if message.startswith("\n"):
        print(f"\n= {message[1:]}")
    else:
        print(f"= {message}")
