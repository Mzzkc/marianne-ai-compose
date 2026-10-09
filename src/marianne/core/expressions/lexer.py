"""Small lexer for the S1 expression grammar."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import ExpressionSyntaxError


class TokenKind(str, Enum):
    IDENT = "ident"
    NUMBER = "number"
    STRING = "string"
    REGEX = "regex"
    SYMBOL = "symbol"
    EOF = "eof"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    value: str
    offset: int


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    offset = 0
    while offset < len(source):
        char = source[offset]
        if char.isspace():
            offset += 1
            continue
        start = offset
        if char.isascii() and (char.isalpha() or char == "_"):
            offset += 1
            while (
                offset < len(source)
                and source[offset].isascii()
                and (source[offset].isalnum() or source[offset] == "_")
            ):
                offset += 1
            tokens.append(Token(TokenKind.IDENT, source[start:offset], start))
            continue
        if char.isascii() and char.isdigit():
            offset += 1
            while offset < len(source) and source[offset].isascii() and source[offset].isdigit():
                offset += 1
            if offset + 1 < len(source) and source[offset] == "." and source[offset + 1].isdigit():
                offset += 1
                while offset < len(source) and source[offset].isdigit():
                    offset += 1
            tokens.append(Token(TokenKind.NUMBER, source[start:offset], start))
            continue
        if char == '"':
            offset += 1
            while offset < len(source):
                if source[offset] == "\\":
                    offset += 2
                elif source[offset] == '"':
                    offset += 1
                    break
                else:
                    offset += 1
            if offset > len(source) or source[offset - 1] != '"':
                raise ExpressionSyntaxError(source, start, "unterminated string")
            tokens.append(Token(TokenKind.STRING, source[start:offset], start))
            continue
        regex_argument = (
            char == "/"
            and len(tokens) >= 2
            and tokens[-1].value == "("
            and tokens[-2].value == "matches"
        )
        if regex_argument:
            offset += 1
            while offset < len(source):
                if source[offset] == "\\" and offset + 1 < len(source):
                    offset += 2
                elif source[offset] == "/":
                    offset += 1
                    break
                else:
                    offset += 1
            if source[offset - 1] != "/" or offset == start + 1:
                raise ExpressionSyntaxError(source, start, "unterminated regex")
            tokens.append(Token(TokenKind.REGEX, source[start + 1 : offset - 1], start))
            continue
        pair = source[offset : offset + 2]
        if pair in {"==", "!=", "<=", ">="}:
            tokens.append(Token(TokenKind.SYMBOL, pair, start))
            offset += 2
            continue
        if char in "().,+-*/%<>":
            tokens.append(Token(TokenKind.SYMBOL, char, start))
            offset += 1
            continue
        raise ExpressionSyntaxError(source, offset, f"unexpected character {char!r}")
    tokens.append(Token(TokenKind.EOF, "", len(source)))
    return tokens
