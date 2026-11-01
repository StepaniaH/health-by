#!/usr/bin/env python3
"""Validate UI literals, locale use, catalog parity and browser catalog output."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_i18n_js import scan as js_scan  # noqa: E402  (本地脚本，无第三方依赖)
HAN = re.compile(r"[\u3400-\u9fff]")
# Explicit non-UI language resources. No file-wide business-module exemptions.
EXEMPTIONS = {
    'health_by/ai/prompt.py': 'Model instructions; translation changes model behavior.',
    'health_by/i18n.py': 'Locale selection and catalog renderer.',
    'health_by/web/js/i18n.js': 'Locale selection and catalog renderer.',
    'health_by/web/js/i18n-messages.js': 'Generated catalog; verified against canonical JSON.',
}
# Proper names, standard units and identifiers are language-invariant.
INVARIANT_TEXT = {'Health By', 'HRV', 'SpO₂', 'ms', 'bpm', 'kg', '°C', '%', '中文', 'English'}


def python_issues(source, file):
    tree = ast.parse(source)
    ignored = set()
    sql_literals = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ignored.add(id(first.value))
        if file == 'health_by/db.py' and isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == 'MIGRATIONS':
            sql_literals.update(id(child) for child in ast.walk(node) if isinstance(child, ast.Constant) and isinstance(child.value, str))
        if file == 'health_by/ai/schema.py' and isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MODEL_WEEKDAYS' for t in node.targets):
            ignored.update(map(id, ast.walk(node)))
        if file == 'health_by/ai/validation.py':
            # These are matching rules for model text, not output messages.
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'FIELD_ALIASES' for t in node.targets):
                ignored.update(map(id, ast.walk(node)))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 're' and node.args:
                ignored.add(id(node.args[0]))
            if isinstance(node, ast.Tuple) and any(isinstance(x, ast.Constant) and x.value == '仅供参考' for x in node.elts):
                ignored.update(map(id, ast.walk(node)))
    issues, keys = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in ignored:
            content = re.sub(r'--[^\n]*', '', node.value) if id(node) in sql_literals else node.value
            if HAN.search(content): issues.append((node.lineno, 'unlocalized string literal'))
            if node.value in ('zh-CN', 'en-US'): issues.append((node.lineno, 'locale outside i18n module'))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 't' and node.args and isinstance(node.args[0], ast.Constant):
            keys.append((node.lineno, node.args[0].value))
    return issues, keys


class MarkupCheck(HTMLParser):
    def __init__(self):
        super().__init__(); self.stack = []; self.issues = []; self.keys = []
    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if self.stack and self.stack[-1][0] == "option":
            self.issues.append((self.getpos()[0], "translation marker must be on the option element"))
        for key, value in attrs.items():
            if key.startswith('data-i18n') and value: self.keys.append((self.getpos()[0], value))
            if value in ('zh-CN', 'en-US'): self.issues.append((self.getpos()[0], 'locale outside i18n module'))
            if key in ('title', 'aria-label', 'placeholder') and value and HAN.search(value) and 'data-i18n-' + key not in attrs:
                self.issues.append((self.getpos()[0], f'unlocalized {key}'))
        if tag not in ('meta','input','link','img','br','hr','path'): self.stack.append((tag, attrs))
    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag: self.stack.pop()
    def handle_data(self, text):
        if HAN.search(text) and text.strip() not in INVARIANT_TEXT and not any('data-i18n' in attrs for _,attrs in self.stack):
            self.issues.append((self.getpos()[0], 'unlocalized markup text'))


def fields(text):
    return {name for _, name, _, _ in Formatter().parse(text) if name is not None}


def collect(root=ROOT):
    problems, references = [], []
    def record(file, issues, keys):
        problems.extend(f'{file}:{line}: {error}' for line, error in issues)
        references.extend((file, line, key) for line, key in keys)
    for path in (root/'health_by').rglob('*'):
        if not path.is_file(): continue
        file = path.relative_to(root).as_posix()
        if file in EXEMPTIONS: continue
        source = path.read_text() if path.suffix in ('.py','.js','.html') else ''
        if path.suffix == '.py': record(file, *python_issues(source,file))
        elif path.suffix == '.js':
            record(file, js_scan(source), [])
        elif path.suffix == '.html':
            check = MarkupCheck(); check.feed(source); record(file, check.issues, check.keys)
    catalogs = {lang: json.loads((root/'health_by/locales'/f'{lang}.json').read_text()) for lang in ('zh','en')}
    if catalogs['zh'].keys() != catalogs['en'].keys(): problems.append('Catalog key sets differ')
    from itertools import product
    plural_parameters = json.loads((root/'health_by/locales/plurals.json').read_text())
    for key, names in plural_parameters.items():
        if key not in catalogs['zh']: problems.append(f'{key}: plural base missing')
        for forms in product(('one','other'), repeat=len(names)):
            variant = '.'.join([key, *forms])
            if variant not in catalogs['zh'] or variant not in catalogs['en']: problems.append(f'{variant}: plural variant missing')
    for key in catalogs['zh'].keys() & catalogs['en'].keys():
        if isinstance(catalogs['en'][key], str) and HAN.search(catalogs['en'][key]):
            problems.append(f'{key}: untranslated English catalog entry')
        if not isinstance(catalogs['en'][key], str):
            problems.append(f'{key}: translation must be a string'); continue
        if re.findall(r'%[-+0-9.#]*[sdf]', catalogs['zh'][key]) != re.findall(r'%[-+0-9.#]*[sdf]', catalogs['en'][key]):
            problems.append(f'{key}: printf placeholders differ')
        try:
            if fields(catalogs['zh'][key]) != fields(catalogs['en'][key]): problems.append(f'{key}: placeholder sets differ')
        except ValueError as exc: problems.append(f'{key}: invalid template: {exc}')
    for file,line,key in references:
        if key not in catalogs['zh'] and not all(key+'.'+plural in catalogs['zh'] for plural in ('one','other')):
            problems.append(f'{file}:{line}: unknown key {key}')
    from build_i18n import generated
    bundle = root/'health_by/web/js/i18n-messages.js'
    if root == ROOT and bundle.read_text() != generated(): problems.append('Browser catalog is stale; run python scripts/build_i18n.py')
    return problems


if __name__ == '__main__':
    errors = collect()
    for error in errors: print(error, file=sys.stderr)
    raise SystemExit(bool(errors))
