#!/usr/bin/env python3
"""Weak-test checker for Python and TypeScript/TSX test files.

The Python channel uses :mod:`ast`.  The dependency-free TypeScript channel
uses a small lexer that understands strings, comments, regular expressions,
templates, and balanced delimiters before inspecting Vitest/Jest callbacks.

Detects tests that contain:
  - ``assert True`` or other tautological assertions (e.g. ``assert 1 == 1``)
  - ``pass`` with no assertions
  - no assertions at all
  - skipped/todo TypeScript tests and suites

Allows real assertions, ``pytest.raises`` blocks, and TypeScript ``expect`` /
``assert`` assertions.  A TypeScript source or callback that cannot be
inspected safely fails closed with an explicit finding.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final


@dataclass(frozen=True)
class WeakTestFinding:
    file: Path
    function_name: str
    line: int
    kind: str
    description: str


@dataclass(frozen=True)
class _TypeScriptToken:
    value: str
    start: int
    end: int
    line: int
    kind: str = "punctuation"


class _TypeScriptParseError(ValueError):
    def __init__(self, message: str, line: int) -> None:
        super().__init__(message)
        self.line = line


_TYPESCRIPT_SUFFIXES: Final = {".ts", ".tsx"}
_OPEN_TO_CLOSE: Final = {"(": ")", "[": "]", "{": "}"}
_CLOSE_TO_OPEN: Final = {value: key for key, value in _OPEN_TO_CLOSE.items()}
_REGEX_PREFIX_TOKENS: Final = {
    "(",
    "[",
    "{",
    ",",
    ";",
    ":",
    "=",
    "==",
    "===",
    "!=",
    "!==",
    "!",
    "?",
    "??",
    "&&",
    "||",
    "=>",
    "+",
    "-",
    "*",
    "%",
    "&",
    "|",
    "^",
    "~",
}
_REGEX_PREFIX_KEYWORDS: Final = {
    "case",
    "delete",
    "in",
    "instanceof",
    "new",
    "return",
    "throw",
    "typeof",
    "void",
    "yield",
}
_MULTI_CHAR_TOKENS: Final = tuple(
    sorted(
        {
            "===",
            "!==",
            ">>>",
            "**=",
            "&&=",
            "||=",
            "??=",
            "=>",
            "==",
            "!=",
            "<=",
            ">=",
            "++",
            "--",
            "&&",
            "||",
            "??",
            "?.",
            "**",
            "<<",
            ">>",
            "+=",
            "-=",
            "*=",
            "/=",
            "%=",
            "&=",
            "|=",
            "^=",
            "...",
        },
        key=len,
        reverse=True,
    )
)
_EQUALITY_MATCHERS: Final = {"toBe", "toEqual", "toStrictEqual"}
_EXPECT_MODIFIERS: Final = {"not", "rejects", "resolves"}
_TEST_IDENTIFIERS: Final = {"it", "test"}
_SUITE_IDENTIFIERS: Final = {"describe", "suite"}
_SKIPPED_TEST_IDENTIFIERS: Final = {"xit", "xtest"}
_SKIPPED_SUITE_IDENTIFIERS: Final = {"xdescribe", "xsuite"}
_ROOT_REGISTRATION_IDENTIFIERS: Final = (
    _TEST_IDENTIFIERS
    | _SUITE_IDENTIFIERS
    | _SKIPPED_TEST_IDENTIFIERS
    | _SKIPPED_SUITE_IDENTIFIERS
)
_SKIP_MODIFIERS: Final = {"skip", "skipIf", "todo"}
_REGULAR_MODIFIERS: Final = {
    "concurrent",
    "each",
    "fails",
    "only",
    "runIf",
    "sequential",
}


def severity_rank(kind: str) -> str:
    """Return severity rank for a weak-test finding kind."""
    return {
        "pass_only": "high",
        "no_assertion": "medium",
        "parse_error": "high",
        "skipped": "high",
        "tautology": "high",
        "uninspectable": "high",
    }.get(kind, "medium")


def _is_identifier_start(character: str) -> bool:
    return character in {"$", "_"} or character.isalpha() or ord(character) >= 128


def _is_identifier_part(character: str) -> bool:
    return _is_identifier_start(character) or character.isdigit()


def _slash_starts_regex(tokens: list[_TypeScriptToken]) -> bool:
    if not tokens:
        return True
    previous = tokens[-1].value
    return previous in _REGEX_PREFIX_TOKENS or previous in _REGEX_PREFIX_KEYWORDS


def _tokenize_typescript(source: str) -> list[_TypeScriptToken]:
    tokens: list[_TypeScriptToken] = []
    index = 0
    line = 1
    length = len(source)

    while index < length:
        character = source[index]
        if character.isspace():
            if character == "\n":
                line += 1
            index += 1
            continue

        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            if newline == -1:
                break
            index = newline
            continue

        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end == -1:
                raise _TypeScriptParseError("unterminated block comment", line)
            comment = source[index : end + 2]
            line += comment.count("\n")
            index = end + 2
            continue

        if character in {"'", '"'}:
            quote = character
            start = index
            start_line = line
            index += 1
            while index < length:
                current = source[index]
                if current == "\\":
                    if index + 1 >= length:
                        raise _TypeScriptParseError("unterminated string literal", start_line)
                    if source[index + 1] == "\n":
                        line += 1
                    index += 2
                    continue
                if current == quote:
                    index += 1
                    tokens.append(
                        _TypeScriptToken(
                            source[start:index], start, index, start_line, "string"
                        )
                    )
                    break
                if current == "\n":
                    raise _TypeScriptParseError("newline in string literal", start_line)
                index += 1
            else:
                raise _TypeScriptParseError("unterminated string literal", start_line)
            continue

        if character == "`":
            start = index
            start_line = line
            index += 1
            while index < length:
                current = source[index]
                if current == "\\":
                    if index + 1 >= length:
                        raise _TypeScriptParseError("unterminated template literal", start_line)
                    if source[index + 1] == "\n":
                        line += 1
                    index += 2
                    continue
                if current == "`":
                    index += 1
                    tokens.append(
                        _TypeScriptToken(
                            source[start:index], start, index, start_line, "template"
                        )
                    )
                    break
                if current == "\n":
                    line += 1
                index += 1
            else:
                raise _TypeScriptParseError("unterminated template literal", start_line)
            continue

        if character == "/" and _slash_starts_regex(tokens):
            start = index
            start_line = line
            index += 1
            in_character_class = False
            while index < length:
                current = source[index]
                if current == "\\":
                    index += 2
                    continue
                if current == "\n":
                    raise _TypeScriptParseError(
                        "unterminated regular expression literal", start_line
                    )
                if current == "[":
                    in_character_class = True
                elif current == "]":
                    in_character_class = False
                elif current == "/" and not in_character_class:
                    index += 1
                    while index < length and source[index].isalpha():
                        index += 1
                    tokens.append(
                        _TypeScriptToken(source[start:index], start, index, start_line, "regex")
                    )
                    break
                index += 1
            else:
                raise _TypeScriptParseError(
                    "unterminated regular expression literal", start_line
                )
            continue

        if _is_identifier_start(character):
            start = index
            start_line = line
            index += 1
            while index < length and _is_identifier_part(source[index]):
                index += 1
            tokens.append(
                _TypeScriptToken(source[start:index], start, index, start_line, "identifier")
            )
            continue

        if character.isdigit():
            start = index
            start_line = line
            index += 1
            while index < length and (
                source[index].isalnum() or source[index] in {".", "_"}
            ):
                index += 1
            tokens.append(
                _TypeScriptToken(source[start:index], start, index, start_line, "number")
            )
            continue

        matched = next(
            (token for token in _MULTI_CHAR_TOKENS if source.startswith(token, index)),
            None,
        )
        if matched is not None:
            tokens.append(
                _TypeScriptToken(matched, index, index + len(matched), line, "operator")
            )
            index += len(matched)
            continue

        tokens.append(_TypeScriptToken(character, index, index + 1, line))
        index += 1

    return tokens


def _delimiter_pairs(tokens: list[_TypeScriptToken]) -> dict[int, int]:
    stack: list[tuple[str, int]] = []
    pairs: dict[int, int] = {}
    for index, token in enumerate(tokens):
        if token.value in _OPEN_TO_CLOSE:
            stack.append((token.value, index))
            continue
        if token.value not in _CLOSE_TO_OPEN:
            continue
        if not stack or stack[-1][0] != _CLOSE_TO_OPEN[token.value]:
            raise _TypeScriptParseError(
                f"unmatched closing delimiter {token.value!r}", token.line
            )
        _, opening_index = stack.pop()
        pairs[opening_index] = index
        pairs[index] = opening_index

    if stack:
        opening, opening_index = stack[-1]
        raise _TypeScriptParseError(
            f"unclosed delimiter {opening!r}", tokens[opening_index].line
        )
    return pairs


def _classify_typescript_registration(
    tokens: list[_TypeScriptToken],
    identifier_index: int,
    pairs: dict[int, int],
) -> tuple[str, bool, int] | None:
    """Return ``(test|suite, skipped, call_open_index)`` for a registration."""
    if identifier_index > 0 and tokens[identifier_index - 1].value in {".", "?."}:
        return None

    identifier = tokens[identifier_index].value
    if identifier in _SKIPPED_TEST_IDENTIFIERS:
        registration_kind = "test"
        skipped = True
    elif identifier in _SKIPPED_SUITE_IDENTIFIERS:
        registration_kind = "suite"
        skipped = True
    elif identifier in _TEST_IDENTIFIERS:
        registration_kind = "test"
        skipped = False
    elif identifier in _SUITE_IDENTIFIERS:
        registration_kind = "suite"
        skipped = False
    else:
        return None

    position = identifier_index + 1
    while position < len(tokens):
        if tokens[position].value == "(":
            return registration_kind, skipped, position
        if tokens[position].value not in {".", "?."} or position + 1 >= len(tokens):
            return None

        modifier = tokens[position + 1].value
        if modifier in _SKIP_MODIFIERS:
            skipped = True
        elif modifier not in _REGULAR_MODIFIERS:
            return None
        position += 2

        if modifier not in {"each", "runIf", "skipIf"}:
            continue
        if position < len(tokens) and tokens[position].value == "(":
            closing = pairs.get(position)
            if closing is None:
                return None
            position = closing + 1
            continue
        if (
            modifier == "each"
            and position < len(tokens)
            and tokens[position].kind == "template"
        ):
            position += 1
            continue
        return None
    return None


def _strip_wrapping_parentheses(
    start: int,
    end: int,
    tokens: list[_TypeScriptToken],
    pairs: dict[int, int],
) -> tuple[int, int]:
    while start < end and tokens[start].value == "(" and pairs.get(start) == end - 1:
        start += 1
        end -= 1
    return start, end


def _top_level_arguments(
    tokens: list[_TypeScriptToken],
    open_index: int,
    close_index: int,
    pairs: dict[int, int],
) -> list[tuple[int, int]]:
    arguments: list[tuple[int, int]] = []
    argument_start = open_index + 1
    position = argument_start
    while position < close_index:
        if tokens[position].value in _OPEN_TO_CLOSE:
            nested_close = pairs.get(position)
            if nested_close is None or nested_close > close_index:
                raise _TypeScriptParseError(
                    "registration contains an invalid delimiter", tokens[position].line
                )
            position = nested_close + 1
            continue
        if tokens[position].value == ",":
            arguments.append((argument_start, position))
            argument_start = position + 1
        position += 1
    if argument_start < close_index:
        arguments.append((argument_start, close_index))
    return [(start, end) for start, end in arguments if start < end]


def _find_callback_body(
    tokens: list[_TypeScriptToken],
    open_index: int,
    close_index: int,
    pairs: dict[int, int],
) -> tuple[int, int] | None:
    for raw_start, raw_end in _top_level_arguments(
        tokens, open_index, close_index, pairs
    ):
        start, end = _strip_wrapping_parentheses(
            raw_start, raw_end, tokens, pairs
        )
        position = start
        while position < end:
            token = tokens[position]
            if token.value in _OPEN_TO_CLOSE:
                nested_close = pairs.get(position)
                if nested_close is None or nested_close >= end:
                    break
                position = nested_close + 1
                continue
            if token.value == "=>":
                body_start = position + 1
                if body_start >= end:
                    return None
                if tokens[body_start].value == "{":
                    body_close = pairs.get(body_start)
                    if body_close is None or body_close >= end:
                        return None
                    return body_start + 1, body_close
                return body_start, end
            if token.value == "function":
                body_position = position + 1
                while body_position < end:
                    if tokens[body_position].value == "(":
                        parameters_close = pairs.get(body_position)
                        if parameters_close is None:
                            return None
                        body_position = parameters_close + 1
                        continue
                    if tokens[body_position].value == "{":
                        body_close = pairs.get(body_position)
                        if body_close is None or body_close >= end:
                            return None
                        return body_position + 1, body_close
                    body_position += 1
                return None
            position += 1
    return None


def _expression_key(
    tokens: list[_TypeScriptToken],
    start: int,
    end: int,
    pairs: dict[int, int],
) -> tuple[str, ...]:
    start, end = _strip_wrapping_parentheses(start, end, tokens, pairs)
    if end - start == 1:
        token = tokens[start]
        if token.kind == "string":
            try:
                value = ast.literal_eval(token.value)
            except (SyntaxError, ValueError):
                pass
            else:
                return ("literal:string", repr(value))
        if token.kind == "number":
            raw_value = token.value.replace("_", "")
            try:
                if raw_value.lower().endswith("n"):
                    value = int(raw_value[:-1], 0)
                    return ("literal:bigint", str(value))
                if raw_value.lower().startswith(("0b", "0o", "0x")):
                    value = int(raw_value, 0)
                    return ("literal:number", str(value))
                value = Decimal(raw_value)
            except (InvalidOperation, ValueError):
                pass
            else:
                return ("literal:number", str(value.normalize()))
        if token.value in {"false", "null", "true", "undefined"}:
            return (f"literal:{token.value}",)
    return tuple(token.value for token in tokens[start:end])


def _call_argument_ranges(
    tokens: list[_TypeScriptToken],
    open_index: int,
    pairs: dict[int, int],
) -> list[tuple[int, int]]:
    close_index = pairs.get(open_index)
    if close_index is None:
        return []
    return _top_level_arguments(tokens, open_index, close_index, pairs)


def _supports_self_comparison(key: tuple[str, ...]) -> bool:
    if not key:
        return False
    if key[0].startswith("literal:"):
        return True
    for index, value in enumerate(key):
        if index % 2 == 1:
            if value not in {".", "?."}:
                return False
            continue
        if not value or not _is_identifier_start(value[0]):
            return False
        if not all(_is_identifier_part(character) for character in value[1:]):
            return False
    return len(key) % 2 == 1


def _expect_assertion(
    tokens: list[_TypeScriptToken],
    expect_index: int,
    body_end: int,
    pairs: dict[int, int],
) -> tuple[bool, bool]:
    call_open = expect_index + 1
    if call_open >= body_end or tokens[call_open].value != "(":
        return False, False
    call_close = pairs.get(call_open)
    if call_close is None or call_close >= body_end:
        return False, False

    negated = False
    matcher: str | None = None
    matcher_open: int | None = None
    position = call_close + 1
    while position + 1 < body_end and tokens[position].value in {".", "?."}:
        chain_name = tokens[position + 1].value
        position += 2
        if chain_name in _EXPECT_MODIFIERS:
            if chain_name == "not":
                negated = not negated
            continue
        if chain_name.startswith("to") and position < body_end:
            if tokens[position].value == "(":
                matcher = chain_name
                matcher_open = position
            break
        break

    if matcher is None or matcher_open is None:
        return False, False

    actual_arguments = _call_argument_ranges(tokens, call_open, pairs)
    matcher_arguments = _call_argument_ranges(tokens, matcher_open, pairs)
    actual_key = (
        _expression_key(tokens, *actual_arguments[0], pairs)
        if len(actual_arguments) == 1
        else ()
    )
    expected_key = (
        _expression_key(tokens, *matcher_arguments[0], pairs)
        if len(matcher_arguments) == 1
        else ()
    )

    tautological = False
    if not negated and matcher in _EQUALITY_MATCHERS:
        tautological = actual_key == expected_key and _supports_self_comparison(
            actual_key
        )
    if (
        not negated
        and actual_key == ("literal:true",)
        and matcher == "toBeTruthy"
    ):
        tautological = True
    if (
        not negated
        and actual_key == ("literal:false",)
        and matcher == "toBeFalsy"
    ):
        tautological = True
    if not negated and actual_key == ("literal:null",) and matcher == "toBeNull":
        tautological = True
    if (
        not negated
        and actual_key == ("literal:undefined",)
        and matcher == "toBeUndefined"
    ):
        tautological = True
    return True, tautological


def _assert_assertion(
    tokens: list[_TypeScriptToken],
    assert_index: int,
    body_end: int,
    pairs: dict[int, int],
) -> tuple[bool, bool]:
    position = assert_index + 1
    method: str | None = None
    if position < body_end and tokens[position].value in {".", "?."}:
        if position + 1 >= body_end:
            return False, False
        method = tokens[position + 1].value
        position += 2
    if position >= body_end or tokens[position].value != "(":
        return False, False

    arguments = _call_argument_ranges(tokens, position, pairs)
    if method is None:
        key = (
            _expression_key(tokens, *arguments[0], pairs)
            if len(arguments) == 1
            else ()
        )
        return True, key == ("literal:true",)

    if method in {"ok", "isTrue"}:
        key = (
            _expression_key(tokens, *arguments[0], pairs)
            if len(arguments) == 1
            else ()
        )
        return True, key == ("literal:true",)

    if method in {"deepEqual", "equal", "strictEqual"} and len(arguments) >= 2:
        left = _expression_key(tokens, *arguments[0], pairs)
        right = _expression_key(tokens, *arguments[1], pairs)
        return True, left == right and _supports_self_comparison(left)
    return True, False


def _typescript_assertion_summary(
    tokens: list[_TypeScriptToken],
    body_start: int,
    body_end: int,
    pairs: dict[int, int],
) -> tuple[int, bool]:
    assertion_count = 0
    has_tautology = False
    for index in range(body_start, body_end):
        token = tokens[index]
        if index > body_start and tokens[index - 1].value in {".", "?."}:
            continue
        if token.value == "expect":
            is_assertion, tautological = _expect_assertion(
                tokens, index, body_end, pairs
            )
        elif token.value == "assert":
            is_assertion, tautological = _assert_assertion(
                tokens, index, body_end, pairs
            )
        else:
            continue
        if is_assertion:
            assertion_count += 1
            has_tautology = has_tautology or tautological
    return assertion_count, has_tautology


def _registration_name(
    tokens: list[_TypeScriptToken],
    identifier_index: int,
    open_index: int,
    pairs: dict[int, int],
) -> str:
    arguments = _call_argument_ranges(tokens, open_index, pairs)
    title = ""
    if arguments:
        start, end = arguments[0]
        if end - start == 1 and tokens[start].kind in {"string", "template"}:
            title = tokens[start].value[1:-1]
    identifier = tokens[identifier_index].value
    return f"{identifier}({title or f'line {tokens[identifier_index].line}'})"


def _read_source_error_finding(
    path: Path,
    language: str,
    error: OSError | UnicodeError,
) -> WeakTestFinding:
    return WeakTestFinding(
        file=path,
        function_name="<module>",
        line=1,
        kind="parse_error",
        description=f"{language} source could not be read as UTF-8: {error}",
    )


def check_typescript_source(path: Path) -> list[WeakTestFinding]:
    """Analyze a TypeScript/TSX test file and fail closed when inspection is unsafe."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [_read_source_error_finding(path, "TypeScript/TSX", exc)]
    try:
        tokens = _tokenize_typescript(source)
        pairs = _delimiter_pairs(tokens)
    except _TypeScriptParseError as exc:
        return [
            WeakTestFinding(
                file=path,
                function_name="<module>",
                line=exc.line,
                kind="parse_error",
                description=f"TypeScript/TSX parse failed: {exc}",
            )
        ]

    findings: list[WeakTestFinding] = []
    saw_test = False
    for identifier_index, token in enumerate(tokens):
        classified = _classify_typescript_registration(
            tokens, identifier_index, pairs
        )
        if classified is None:
            previous_value = (
                tokens[identifier_index - 1].value if identifier_index > 0 else None
            )
            next_value = (
                tokens[identifier_index + 1].value
                if identifier_index + 1 < len(tokens)
                else None
            )
            if (
                token.value in _ROOT_REGISTRATION_IDENTIFIERS
                and previous_value not in {".", "?."}
                and next_value in {"(", ".", "?."}
            ):
                if token.value in _TEST_IDENTIFIERS | _SKIPPED_TEST_IDENTIFIERS:
                    saw_test = True
                findings.append(
                    WeakTestFinding(
                        file=path,
                        function_name=f"{token.value}(line {token.line})",
                        line=token.line,
                        kind="uninspectable",
                        description=(
                            "unsupported test or suite registration syntax: "
                            f"{token.value} at line {token.line}"
                        ),
                    )
                )
            continue
        registration_kind, skipped, open_index = classified
        close_index = pairs.get(open_index)
        if close_index is None:
            continue
        name = _registration_name(
            tokens, identifier_index, open_index, pairs
        )

        if registration_kind == "suite":
            if skipped:
                findings.append(
                    WeakTestFinding(
                        file=path,
                        function_name=name,
                        line=token.line,
                        kind="skipped",
                        description=f"skipped test suite: {name}",
                    )
                )
            continue

        saw_test = True
        if skipped:
            findings.append(
                WeakTestFinding(
                    file=path,
                    function_name=name,
                    line=token.line,
                    kind="skipped",
                    description=f"skipped or todo test: {name}",
                )
            )
            continue

        callback_body = _find_callback_body(
            tokens, open_index, close_index, pairs
        )
        if callback_body is None:
            findings.append(
                WeakTestFinding(
                    file=path,
                    function_name=name,
                    line=token.line,
                    kind="uninspectable",
                    description=f"test callback cannot be inspected: {name}",
                )
            )
            continue
        body_start, body_end = callback_body
        if body_start == body_end:
            findings.append(
                WeakTestFinding(
                    file=path,
                    function_name=name,
                    line=token.line,
                    kind="pass_only",
                    description=f"empty or comment-only test body: {name}",
                )
            )
            continue

        assertion_count, has_tautology = _typescript_assertion_summary(
            tokens, body_start, body_end, pairs
        )
        if assertion_count == 0:
            findings.append(
                WeakTestFinding(
                    file=path,
                    function_name=name,
                    line=token.line,
                    kind="no_assertion",
                    description=f"no assertion in test callback: {name}",
                )
            )
        if has_tautology:
            findings.append(
                WeakTestFinding(
                    file=path,
                    function_name=name,
                    line=token.line,
                    kind="tautology",
                    description=f"tautology in test callback: {name}",
                )
            )

    if not saw_test:
        findings.append(
            WeakTestFinding(
                file=path,
                function_name="<module>",
                line=1,
                kind="uninspectable",
                description="no supported Vitest/Jest test registration found",
            )
        )
    return findings


def _is_tautological_assert(node: ast.Assert) -> bool:
    """Check if an assert statement is tautological."""
    test = node.test
    # assert True
    if isinstance(test, ast.Constant) and test.value is True:
        return True
    # assert 1 == 1, assert "a" == "a", etc.
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        if isinstance(test.ops[0], (ast.Eq, ast.Is)):
            left = test.left
            right = test.comparators[0]
            if isinstance(left, ast.Constant) and isinstance(right, ast.Constant):
                if left.value == right.value:
                    return True
            # assert value == value (same name on both sides)
            if isinstance(left, ast.Name) and isinstance(right, ast.Name):
                if left.id == right.id:
                    return True
            # assert obj.attr == obj.attr (same dotted name)
            if isinstance(left, ast.Attribute) and isinstance(right, ast.Attribute):
                if ast.dump(left) == ast.dump(right):
                    return True
    return False


def _has_pytest_raises(node: ast.AST) -> bool:
    """Check if the AST subtree contains a ``pytest.raises`` context manager."""
    for child in ast.walk(node):
        if isinstance(child, ast.With):
            for item in child.items:
                ctx = item.context_expr
                # pytest.raises(...)
                if isinstance(ctx, ast.Call):
                    func = ctx.func
                    if isinstance(func, ast.Attribute) and func.attr == "raises":
                        return True
                    if isinstance(func, ast.Name) and func.id == "raises":
                        return True
    return False


def _count_real_assertions(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count non-tautological assert statements and pytest.raises blocks."""
    count = 0
    for child in ast.walk(node):
        if isinstance(child, ast.Assert) and not _is_tautological_assert(child):
            count += 1
        elif isinstance(child, ast.With) and _has_pytest_raises(child):
            count += 1
    return count


def _is_pass_only(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if the function body is only ``pass`` (plus docstring)."""
    meaningful = [
        stmt for stmt in node.body
        if not isinstance(stmt, ast.Pass)
        and not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
    ]
    return len(meaningful) == 0


def _check_python_source(path: Path) -> list[WeakTestFinding]:
    """Analyze a Python test file and return weak-test findings."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [_read_source_error_finding(path, "Python", exc)]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [
            WeakTestFinding(
                file=path,
                function_name="<module>",
                line=exc.lineno or 1,
                kind="parse_error",
                description=f"Python parse failed: {exc.msg}",
            )
        ]
    findings: list[WeakTestFinding] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue

        # Check for pass-only
        if _is_pass_only(node):
            findings.append(WeakTestFinding(
                file=path,
                function_name=node.name,
                line=node.lineno,
                kind="pass_only",
                description=f"pass-only test function: {node.name}",
            ))
            continue

        # Count real assertions (includes pytest.raises)
        real_assertions = _count_real_assertions(node)

        if real_assertions == 0:
            # Check if there are any tautological asserts
            has_tautological = False
            for child in ast.walk(node):
                if isinstance(child, ast.Assert) and _is_tautological_assert(child):
                    has_tautological = True
                    break

            if has_tautological:
                findings.append(WeakTestFinding(
                    file=path,
                    function_name=node.name,
                    line=node.lineno,
                    kind="tautology",
                    description=(
                        f"tautology or assert True in: {node.name}"
                    ),
                ))
            else:
                findings.append(WeakTestFinding(
                    file=path,
                    function_name=node.name,
                    line=node.lineno,
                    kind="no_assertion",
                    description=f"no assertion in test function: {node.name}",
                ))
        else:
            # Has real assertions — but also check for tautological ones
            has_tautological = False
            for child in ast.walk(node):
                if isinstance(child, ast.Assert) and _is_tautological_assert(child):
                    has_tautological = True
                    break
            if has_tautological:
                findings.append(WeakTestFinding(
                    file=path,
                    function_name=node.name,
                    line=node.lineno,
                    kind="tautology",
                    description=(
                        f"tautology alongside real assertions in: "
                        f"{node.name}"
                    ),
                ))

    return findings


def check_source(path: Path) -> list[WeakTestFinding]:
    """Analyze one supported test file and return deterministic findings."""
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _check_python_source(path)
    if suffix in _TYPESCRIPT_SUFFIXES:
        return check_typescript_source(path)
    return [
        WeakTestFinding(
            file=path,
            function_name="<module>",
            line=1,
            kind="parse_error",
            description=f"unsupported test file extension: {suffix or '<none>'}",
        )
    ]


def check_directory(directory: Path) -> list[WeakTestFinding]:
    """Check supported Python and TypeScript test files recursively."""
    findings: list[WeakTestFinding] = []
    candidates = set(directory.rglob("test_*.py"))
    for pattern in (
        "*.test.ts",
        "*.test.tsx",
        "*.spec.ts",
        "*.spec.tsx",
        "test_*.ts",
        "test_*.tsx",
    ):
        candidates.update(directory.rglob(pattern))
    for test_file in sorted(candidates):
        findings.extend(check_source(test_file))
    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check Python and TypeScript/TSX test files for weak-test patterns"
            " (tautologies, skipped tests, pass-only, no assertions)."
        )
    )
    parser.add_argument(
        "path",
        type=Path,
        help="File or directory to scan.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target = args.path.resolve()
    if target.is_file():
        findings = check_source(target)
    elif target.is_dir():
        findings = check_directory(target)
    else:
        print(f"Path not found: {target}", file=sys.stderr)
        return 2

    if findings:
        print(f"Weak-test check failed. {len(findings)} finding(s):", file=sys.stderr)
        for f in findings:
            print(
                f"  [{severity_rank(f.kind)}] {f.file}:{f.line} — {f.description}",
                file=sys.stderr,
            )
        return 1

    print("Weak-test check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
