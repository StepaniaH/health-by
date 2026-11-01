"""Dependency-free checks for UI literals, locale calls and template fragments."""
from __future__ import annotations

from bisect import bisect_right
from pathlib import Path
import re
import sys

HAN = re.compile(r"[\u3400-\u9fff\U00020000-\U000323af]")
LOCALE_LITERAL = re.compile(r"^(?:zh-CN|en-US)$", re.IGNORECASE)
LOCALE_CALLS = {"toLocaleString", "toLocaleDateString", "toLocaleTimeString"}
EXEMPT = {Path('health_by/web/js/i18n.js'), Path('health_by/web/js/i18n-messages.js')}
REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%~^")
REGEX_KEYWORDS = {'return', 'throw', 'case', 'yield', 'typeof', 'void', 'delete'}


def scan(source: str) -> list[tuple[int, str]]:
    problems = []
    newlines = [index for index, char in enumerate(source) if char == '\n']
    index, length = 0, len(source)

    def report(start, message):
        problems.append((bisect_right(newlines, start) + 1, message))

    def check_literal(value, start):
        # Combine escaped surrogate pairs without interpreting a literal backslash twice.
        try:
            value = value.encode('utf-16', 'surrogatepass').decode('utf-16')
        except UnicodeError:
            pass
        if HAN.search(value): report(start, 'unlocalized string literal')
        if LOCALE_LITERAL.fullmatch(value): report(start, 'locale outside i18n module')

    def escape():
        nonlocal index
        index += 1
        if index >= length: return ''
        char = source[index]; index += 1
        if char in '\r\n':
            if char == '\r' and index < length and source[index] == '\n': index += 1
            return ''
        if char in ('u', 'x'):
            if char == 'u' and index < length and source[index] == '{':
                end = source.find('}', index + 1)
                if end >= 0:
                    digits = source[index + 1:end]; index = end + 1
                else: return char
            else:
                count = 4 if char == 'u' else 2
                digits = source[index:index + count]; index += len(digits)
            try: return chr(int(digits, 16))
            except (ValueError, OverflowError): return digits
        return {'n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','v':'\v','0':'\0'}.get(char, char)

    def quoted():
        nonlocal index
        start, quote = index, source[index]; index += 1
        text = []
        while index < length:
            char = source[index]
            if char == '\\': text.append(escape()); continue
            index += 1
            if char == quote: break
            text.append(char)
        value = ''.join(text); check_literal(value, start)
        return ('string', value, start)

    def regex():
        nonlocal index
        index += 1; in_class = False
        while index < length:
            char = source[index]; index += 1
            if char == '\\': index += 1
            elif char == '[': in_class = True
            elif char == ']': in_class = False
            elif char == '/' and not in_class: break
        while index < length and source[index].isalpha(): index += 1

    def template():
        nonlocal index
        start = index; index += 1
        text, nested = [], []
        while index < length:
            char = source[index]
            if char == '\\': text.append(escape()); continue
            if char == '`': index += 1; break
            if source.startswith('${', index):
                index += 2; expression = code(stop_at_brace=True)
                nested.extend(expression)
                questions = [i for i, token in enumerate(expression) if token[0] == 'operator' and token[1] == '?']
                if questions and any(token[0] in ('string','template') and token[1] not in ('', ' ', '—', '-') for token in expression[questions[0] + 1:]):
                    report(start, 'conditional fragment inside template literal')
                continue
            text.append(char); index += 1
        value = ''.join(text); check_literal(value, start)
        return [('template', value, start), *nested]

    def code(stop_at_brace=False):
        nonlocal index
        tokens, braces, previous = [], 0, None
        while index < length:
            char = source[index]
            if char.isspace(): index += 1; continue
            if source.startswith('//', index):
                end = source.find('\n', index); index = length if end < 0 else end; continue
            if source.startswith('/*', index):
                end = source.find('*/', index + 2); index = length if end < 0 else end + 2; continue
            if char == '/' and (previous is None or previous[1] in REGEX_PRECEDERS or previous[1] in REGEX_KEYWORDS):
                start = index; regex(); previous = ('regex','/',start); tokens.append(previous); continue
            if char in ('"', "'"):
                previous = quoted(); tokens.append(previous); continue
            if char == '`':
                part = template(); tokens.extend(part); previous = part[0]; continue
            if char == '}' and stop_at_brace and braces == 0:
                index += 1; break
            if char == '{': braces += 1
            elif char == '}': braces -= 1
            start = index
            if char.isalpha() or char in '_$':
                index += 1
                while index < length and (source[index].isalnum() or source[index] in '_$'): index += 1
                word = source[start:index]
                if word in LOCALE_CALLS: report(start, 'locale formatting outside i18n module')
                previous = ('identifier',word,start)
            elif char.isdigit():
                index += 1
                while index < length and (source[index].isalnum() or source[index] in '._'): index += 1
                previous = ('number',source[start:index],start)
            else:
                operator = source[index:index + 2]
                if operator in ('?.','??'): index += 2
                else: operator = char; index += 1
                previous = ('operator',operator,start)
            tokens.append(previous)
        return tokens

    code()
    return sorted(set(problems))


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else Path('health_by/web/js')
    failed = False
    for path in sorted(root.glob('*.js')):
        if path in EXEMPT: continue
        for line, problem in scan(path.read_text(encoding='utf-8')):
            print(f'{path}:{line}: {problem}'); failed = True
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
