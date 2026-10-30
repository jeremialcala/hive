# -*- coding: utf-8 -*-
"""
    What a client may write into the configuration of a chatbot: its persona and its rules, which the engine puts in the
    `system` of the model (RS11, RF23, RF24; decisions of Jeremi of 2026-10-30).

    The client writes its own instructions, so what they say is not judged here: the guardrails of the platform go above
    them (RF27). What is refused is what could break the shape of the `system`:

    * **Control characters.** A line break only where text has lines (the description and the rules), and a tab there
      too. Never the characters that turn text around or hide it (bidirectional overrides, zero-width marks).
    * **The marks with which a model tells roles apart**: `<|...|>`, `[INST]`, `<<SYS>>`, `<s>` and `</s>`. Written in
      a rule, they could open a turn of the user or of the system that the platform did not write.
"""
import re
import unicodedata

# The limits of the persona and of a rule (RS11, the generous ones).
NAME_LENGTH = 100
DESCRIPTION_LENGTH = 2000
RULE_LENGTH = 1000
RULES_PER_BOT = 50

TEMPLATE_MARK = re.compile(r"<\|[^|>]*\|>|\[/?INST\]|<</?SYS>>|</?s>", re.IGNORECASE)
# Format characters that change how text reads without being seen.
HIDDEN = {"​", "‌", "‍", "‎", "‏", "⁠", "﻿",
          *(chr(code) for code in range(0x202a, 0x202f)), *(chr(code) for code in range(0x2066, 0x206a))}


def _check(value: str, lines: bool) -> str:
    allowed = {"\n", "\t"} if lines else set()
    for character in value:
        if character in allowed:
            continue
        if unicodedata.category(character) == "Cc" or character in HIDDEN or character in (" ", " "):
            raise ValueError("The text cannot carry control characters" + ("" if lines else " nor line breaks"))
    if TEMPLATE_MARK.search(value):
        raise ValueError("The text cannot carry the marks a model separates roles with")
    return value


def one_line(value: str) -> str:
    """A name, a role or a tone: one line with something in it."""
    if not value.strip():
        raise ValueError("The text cannot be blank")
    return _check(value, lines=False)


def lines(value: str) -> str:
    """A description: lines, possibly none."""
    return _check(value, lines=True)


def rule(value: str) -> str:
    """A rule: lines with something in them."""
    if not value.strip():
        raise ValueError("A rule cannot be blank")
    return _check(value, lines=True)
