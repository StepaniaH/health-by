"""Language negotiation, concurrent rendering and untranslated-source guards."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import re

import pytest
from pathlib import Path
import subprocess
import tempfile
import sys

from click.testing import CliRunner
from fastapi.testclient import TestClient

from health_by.i18n import CATALOGS, language, localize, t, using_language

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from check_i18n import MarkupCheck, python_issues


def test_request_languages_do_not_freeze_imported_labels_or_cross_threads():
    imported = t('server.analysis._baseline_body.2986c7ee9')
    def render(lang):
        with using_language(lang):
            return localize(deepcopy({'label': imported}))['label']
    with ThreadPoolExecutor(max_workers=2) as pool:
        rendered = list(pool.map(render, ['en', 'zh'] * 12))
    assert rendered == [CATALOGS['en'][imported.key], CATALOGS['zh'][imported.key]] * 12
    assert language() == 'zh'


def test_backend_negotiates_errors_and_does_not_modify_stored_or_model_text(tmp_cfg):
    from health_by.api.app import create_app
    app = create_app(tmp_cfg)
    try:
        client = TestClient(app)
        en = client.get('/api/v1/summary?date=invalid', headers={'accept-language':'en-US,en;q=0.9'})
        zh = client.get('/api/v1/summary?date=invalid', headers={'accept-language':'zh-CN'})
        assert en.status_code == zh.status_code == 400
        assert en.headers['content-language'] == 'en'
        assert en.json()['detail'] != zh.json()['detail']
        assert 'YYYY-MM-DD' in en.json()['detail']
        assert localize('原始文本 <script>') == '原始文本 <script>'
    finally:
        app.state.engine.shutdown(); app.state.engine.close(); app.state.conn.close()


def test_cli_localizes_help_and_preserves_machine_command_names(tmp_cfg):
    from health_by.cli import main
    runner = CliRunner()
    en = runner.invoke(main, ['--lang','en','--help'])
    zh = runner.invoke(main, ['--lang','zh','--help'])
    assert en.exit_code == zh.exit_code == 0
    assert 'serve' in en.output and 'sync' in en.output
    assert en.output != zh.output
    assert not re.search(r'[\u3400-\u9fff]', en.output)
    help_serve = runner.invoke(main, ['--lang','en','serve','--help'])
    assert help_serve.exit_code == 0
    assert not re.search(r'[\u3400-\u9fff]', help_serve.output)


def test_source_guards_distinguish_comments_from_literals_and_do_not_exempt_ui():
    assert python_issues('# 中文注释\n"""中文文档"""\nx = t("label")', 'module.py')[0] == []
    assert python_issues('x = "未翻译"', 'module.py')[0]
    assert python_issues('x = "en-US"', 'module.py')[0]
    assert python_issues('"""注释"""\nx = "未翻译"', 'health_by/ai/validation.py')[0]
    check = MarkupCheck();check.feed('<span data-i18n="label">中文</span>')
    assert not check.issues and check.keys == [(1,'label')]
    check = MarkupCheck();check.feed('<button title="未翻译">按钮</button>')
    assert len(check.issues) == 2
    negative = '// 中文注释\nconst a="未翻译"; n.toLocaleString("en-US"); const c="zh-CN";'
    positive = '// 中文注释\nconst a=t("label",{n:42}); const b=/中文/; const d=`x${ok ? 1 : 0}k`;'
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)
        (path / "negative.js").write_text(negative, encoding="utf-8")
        (path / "positive.js").write_text(positive, encoding="utf-8")
        bad = subprocess.run([sys.executable, "scripts/check_i18n_js.py", folder], cwd=ROOT, text=True, capture_output=True)
        assert bad.returncode == 1
        assert "unlocalized string literal" in bad.stdout
        assert "locale outside i18n module" in bad.stdout
        assert "locale formatting outside i18n module" in bad.stdout
        (path / "negative.js").unlink()
        good = subprocess.run([sys.executable, "scripts/check_i18n_js.py", folder], cwd=ROOT, text=True, capture_output=True)
        assert good.returncode == 0, good.stdout


def test_browser_language_priority_parameters_plural_dates_and_text_safety():
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function load(search,stored,browser) {
 const box={URLSearchParams, location:{search},navigator:{language:browser},localStorage:{getItem(){return stored;}}};
 vm.createContext(box);
 for(const file of ['i18n-messages.js','i18n.js'])vm.runInContext(fs.readFileSync('health_by/web/js/'+file,'utf8').replace(/^import .*;$/gm,'').replace(/^export /gm,''),box);
 return box;
}
const en=load('?lang=en','zh','zh-CN');
assert.equal(vm.runInContext('language',en),'en');
assert.equal(vm.runInContext('language',load('?lang=invalid','en','zh-CN')),'en');
assert.equal(vm.runInContext('language',load('','invalid','en-US')),'en');
assert.equal(vm.runInContext('language',load('','invalid','fr-FR')),'zh');
assert.equal(en.t('ai.meta.attempts',{n:1,count:1}),'1 request');
assert.equal(en.t('ai.meta.attempts',{n:2,count:2}),'2 requests');
assert.equal(en.t('ai.meta.model',{model:'<img onerror="x">'}),'<img onerror="x">');
assert.equal(en.formatNumber(1234),'1,234');
assert.equal(en.formatDateTitle('2026-11-01'),'Nov 1, 2026 · Sunday');
assert.equal(en.duration(441),'7 h 21 min');
assert.equal(en.formatDateTime(null),'—');
const zh=load('?lang=zh',null,'en-US');
assert.equal(zh.formatNumber(1234),'1,234');
assert.equal(zh.duration(42),'42 分钟');
'''
    result = subprocess.run(['node','-e',script],cwd=ROOT,text=True,capture_output=True,timeout=10)
    assert result.returncode == 0, result.stderr

def test_complete_plural_variants_and_formatted_counts():
    key = 'server.cli.status.c32ceff36'
    with using_language('en'):
        assert '1 day' in str(t(key, p1=1))
        assert '2 days' in str(t(key, p1=2))
        assert '1,234 days' in str(t(key, p1=1234))
    with using_language('zh'):
        assert '1,234' in str(t(key, p1=1234))


def test_new_confirmation_token_is_independent_of_language(tmp_cfg):
    from health_by.api.app import create_app
    app = create_app(tmp_cfg)
    try:
        client = TestClient(app)
        for lang in ('en','zh'):
            assert client.post('/api/v1/data/clear', headers={'accept-language':lang},json={'confirm':'删除全部数据'}).status_code == 400
            assert client.post('/api/v1/data/clear', headers={'accept-language':lang},json={'confirm':'DELETE ALL DATA'}).status_code == 200
    finally:
        app.state.engine.shutdown(); app.state.engine.close(); app.state.conn.close()


def test_model_input_is_independent_of_ui_language():
    from health_by.ai.service import build_payload, build_window_payload, payload_audit
    from health_by.ai.prompt import build_messages
    day = {'date':'2024-02-01', 'activity':{'steps':3456}, 'heart':{'hrv_ms':44.4}, 'sleep':{'total_minutes':431}}
    snapshots = []
    for lang in ('zh','en'):
        with using_language(lang):
            daily = build_payload(day, {}, {}, {}, today='2024-02-01',history=[day])
            window = build_window_payload([day], {}, '2024-02-01','2024-02-02',today='2024-02-02')
            snapshots.append((json.dumps(daily,ensure_ascii=False,sort_keys=True),payload_audit(window),build_messages(daily),build_messages(window)))
    assert snapshots[0] == snapshots[1]


def test_option_markers_survive_browser_option_parsing():
    for name in ('index.html','settings.html'):
        source = (ROOT/'health_by/web'/name).read_text()
        assert not re.search(r'<option\b[^>]*>\s*<',source)
    check = MarkupCheck();check.feed('<select><option><span data-i18n="key">中文</span></option></select>')
    assert any('option element' in text for _,text in check.issues)


@pytest.mark.parametrize('source,expected', [
    ('const x = `${ok ? "yes" : "no"}`;', 'conditional fragment inside template literal'),
    ('const x = `${"中文"}`;', 'unlocalized string literal'),
    (r'const x = "\u4e2d";', 'unlocalized string literal'),
    (r'const x = `\u{4e2d}`;', 'unlocalized string literal'),
    ('const x = `${f({value:"中文"})}`;', 'unlocalized string literal'),
    ('const x = `${value.toLocaleString("en-US")}`;', 'locale outside i18n module'),
])
def test_dependency_free_scanner_covers_interpolation_and_escapes(source, expected):
    from check_i18n_js import scan
    assert expected in {message for _,message in scan(source)}


@pytest.mark.parametrize('source', [
    '// 中文\nconst x = /[\\/中文]/;',
    'function f(){ return /中文/; }',
    'const x = `${ok ? 1 : 0}k`;',
    'const x = `${value?.n ?? 0}`;',
    'const x = `${t("label", {n:1})}`;',
    r'const x = "\\u4e2d";',
])
def test_dependency_free_scanner_preserves_non_ui_text_and_numeric_expressions(source):
    from check_i18n_js import scan
    assert scan(source) == []
