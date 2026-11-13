import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import dotenv
import IPython.display
import openai
import pytest

NOTEBOOK = Path(__file__).resolve().parents[2] / "labs" / "volcengine.ipynb"


@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_volcengine_renders_full_content_and_reports_truncation(
    tmp_path, monkeypatch, capsys, finish_reason
):
    text = "First paragraph.\n\n" + "Complete response text. " * 300 + "Last paragraph."
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text), finish_reason=finish_reason
            )
        ]
    )
    client = Mock()
    client.chat.completions.create.return_value = response
    factory = Mock(return_value=client)
    render = Mock()
    monkeypatch.setattr(openai, "OpenAI", factory)
    monkeypatch.setattr(dotenv, "load_dotenv", Mock())
    monkeypatch.setattr(IPython.display, "display", render)
    monkeypatch.setenv("ARK_API_KEY", "test-key")

    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    source = "\n\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )
    script = tmp_path / "volcengine_lab.py"
    script.write_text(source, encoding="utf-8")
    result = runpy.run_path(str(script))

    assert result["content"] == text
    render.assert_called_once()
    assert render.call_args.args[0].data == text
    client.chat.completions.create.assert_called_once()
    request = client.chat.completions.create.call_args.kwargs
    assert request["max_tokens"] == 4096
    factory.assert_called_once_with(
        api_key="test-key", base_url="https://ark.cn-beijing.volces.com/api/v3"
    )
    output = capsys.readouterr().out
    assert f"finish_reason: {finish_reason}" in output
    assert ("输出达到长度上限" in output) == (finish_reason == "length")
